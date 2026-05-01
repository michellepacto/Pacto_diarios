"""
Vercel Serverless Function — Analisa UMA portaria com IA (Claude Sonnet).

Endpoint: POST /api/analisar
Recebe:   JSON com {"id": 42}
Retorna:  JSON com os campos gerados pela IA já salvos no banco

Processa uma por vez para caber no timeout do Vercel (10s no plano Hobby).
O frontend chama em loop com progress bar.
"""

import os
import re
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

from supabase import create_client


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODELO = "claude-sonnet-4-5-20250929"


PROMPT_USUARIO = """Você é um especialista em legislação educacional de Minas Gerais.

Analise esta portaria da Assessoria de Inspeção Escolar do Diário Oficial de MG e extraia as informações abaixo.

PORTARIA:
{texto}

Responda APENAS com este JSON, sem markdown, sem ```json, sem explicações antes ou depois:
{{
  "escola_nome": "nome completo do estabelecimento de ensino mencionado (escola, colégio, centro educacional). Pegue o nome COMPLETO como aparece no texto. Use null se realmente não houver escola.",
  "resumo": "1-2 frases simples explicando o que esta portaria faz",
  "prazo": "prazo em anos se mencionado (ex: '5 anos', '3 anos'). Use null se não houver prazo.",
  "observacoes": "informação adicional relevante: mantenedora, motivo, novos cursos, etc. Use null se não houver."
}}"""


def chamar_claude(texto: str) -> dict:
    """Chama a API do Claude e retorna o JSON analisado."""
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY não configurada nas variáveis de ambiente do Vercel")

    prompt = PROMPT_USUARIO.format(texto=texto[:3000])

    payload = json.dumps({
        "model": MODELO,
        "max_tokens": 512,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read())

    # Extrair texto da resposta
    conteudo = data.get("content", [])
    texto_resposta = ""
    for bloco in conteudo:
        if bloco.get("type") == "text":
            texto_resposta = bloco.get("text", "").strip()
            break

    # Limpar possíveis marcadores markdown
    texto_resposta = re.sub(r'^```(?:json)?\s*', '', texto_resposta)
    texto_resposta = re.sub(r'\s*```$', '', texto_resposta)

    return json.loads(texto_resposta)


class handler(BaseHTTPRequestHandler):

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        # Validar configuração
        if not SUPABASE_URL or not SUPABASE_KEY:
            self._json(500, {"erro": "Supabase não configurado"})
            return
        if not ANTHROPIC_API_KEY:
            self._json(500, {
                "erro": "ANTHROPIC_API_KEY não configurada",
                "detalhe": "Adicione ANTHROPIC_API_KEY nas Environment Variables do Vercel"
            })
            return

        # Ler body
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
        except (json.JSONDecodeError, ValueError):
            self._json(400, {"erro": "JSON inválido"})
            return

        portaria_id = body.get("id")
        if not portaria_id:
            self._json(400, {"erro": "Forneça o campo 'id' da portaria"})
            return

        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

            # Buscar a portaria
            result = (
                supabase.table("portarias_inspecao")
                .select("id,numero_completo,texto_completo,escola_nome")
                .eq("id", portaria_id)
                .limit(1)
                .execute()
            )

            if not result.data:
                self._json(404, {"erro": f"Portaria id={portaria_id} não encontrada"})
                return

            portaria = result.data[0]
            texto = portaria.get("texto_completo") or ""
            if not texto.strip():
                self._json(400, {"erro": "Portaria sem texto_completo para analisar"})
                return

            # Chamar Claude
            ia = chamar_claude(texto)

            # Preparar campos pra salvar
            campos = {
                "escola_nome_ia": ia.get("escola_nome") or None,
                "resumo_ia":      ia.get("resumo") or None,
                "prazo_ia":       ia.get("prazo") or None,
                "observacoes_ia": ia.get("observacoes") or None,
                "analisado_em":   datetime.now(timezone.utc).isoformat(),
            }

            # Salvar no banco
            supabase.table("portarias_inspecao") \
                .update(campos) \
                .eq("id", portaria_id) \
                .execute()

            self._json(200, {
                "id": portaria_id,
                "numero_completo": portaria.get("numero_completo"),
                **campos,
            })

        except urllib.error.HTTPError as e:
            corpo = e.read().decode("utf-8", errors="replace")
            self._json(502, {
                "erro": f"Erro da API Anthropic ({e.code})",
                "detalhe": corpo[:500],
            })
        except json.JSONDecodeError as e:
            self._json(502, {
                "erro": "Resposta da IA não é um JSON válido",
                "detalhe": str(e),
            })
        except Exception as e:
            self._json(500, {"erro": str(e)})
