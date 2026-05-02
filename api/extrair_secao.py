"""
Endpoint /api/extrair_secao
Extrai a SECAO de Inspecao Escolar de um PDF SEM salvar no banco.
Retorna o trecho de texto puro para ser analisado pela IA na aba "Formato Novo".
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs
import json
import os
import sys
import tempfile
import cgi

# Importa funcoes do processar.py (mesmo diretorio)
try:
    from .processar import (
        extrair_texto_pdf,
        localizar_secao,
        normalizar_texto,
        quebrar_em_portarias,
        extrair_data_diario,
    )
except ImportError:
    # Fallback para Vercel (pode nao reconhecer import relativo)
    sys.path.insert(0, os.path.dirname(__file__))
    from processar import (
        extrair_texto_pdf,
        localizar_secao,
        normalizar_texto,
        quebrar_em_portarias,
        extrair_data_diario,
    )


class handler(BaseHTTPRequestHandler):

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def _json_response(self, status, data):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode('utf-8'))

    def do_POST(self):
        try:
            ctype = self.headers.get('content-type', '')
            length = int(self.headers.get('content-length', 0))

            if 'multipart/form-data' not in ctype:
                return self._json_response(400, {"erro": "Use multipart/form-data com campo 'file'"})

            # Parsing do upload multipart
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={'REQUEST_METHOD': 'POST', 'CONTENT_TYPE': ctype, 'CONTENT_LENGTH': str(length)},
            )

            if 'file' not in form:
                return self._json_response(400, {"erro": "Arquivo nao enviado"})

            field = form['file']
            nome_arquivo = field.filename or 'desconhecido.pdf'
            conteudo = field.file.read()

            # Salva em tempfile para passar pra extrair_texto_pdf
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                tmp.write(conteudo)
                caminho = tmp.name

            try:
                texto_completo, paginas, metadata_title = extrair_texto_pdf(caminho)
            finally:
                try:
                    os.unlink(caminho)
                except Exception:
                    pass

            if not texto_completo:
                return self._json_response(200, {
                    "ok": False,
                    "arquivo": nome_arquivo,
                    "secao_encontrada": False,
                    "mensagem": "Nao foi possivel extrair texto do PDF",
                })

            data_diario = extrair_data_diario(nome_arquivo, texto_completo, metadata_title)
            texto_secao, pagina_secao = localizar_secao(texto_completo, paginas)

            if not texto_secao:
                return self._json_response(200, {
                    "ok": True,
                    "arquivo": nome_arquivo,
                    "data_diario": data_diario.isoformat() if data_diario else None,
                    "secao_encontrada": False,
                    "trecho": "",
                    "mensagem": "Secao 'Assessoria de Inspecao Escolar' nao encontrada neste PDF",
                })

            # Tenta tambem quebrar em portarias (so pra contar)
            texto_normalizado = normalizar_texto(texto_secao)
            try:
                portarias = quebrar_em_portarias(texto_normalizado)
                qtd_portarias_extraidas = len(portarias)
            except Exception:
                portarias = []
                qtd_portarias_extraidas = 0

            return self._json_response(200, {
                "ok": True,
                "arquivo": nome_arquivo,
                "data_diario": data_diario.isoformat() if data_diario else None,
                "secao_encontrada": True,
                "pagina_inicio": pagina_secao,
                "tamanho_secao_chars": len(texto_secao),
                "qtd_portarias_extraidas_pelo_codigo_atual": qtd_portarias_extraidas,
                "trecho": texto_secao[:15000],  # limita pra resposta nao ficar gigante
                "trecho_truncado": len(texto_secao) > 15000,
            })

        except Exception as e:
            import traceback
            return self._json_response(500, {
                "erro": str(e),
                "trace": traceback.format_exc()[:1000],
            })
