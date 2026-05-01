"""
Vercel Serverless Function - Processa um PDF do Diário Oficial de MG
e insere portarias da Assessoria de Inspeção Escolar no Supabase.

Endpoint: POST /api/processar
Recebe:   FormData com campo 'pdf' (file)
Retorna:  JSON com lista de portarias extraídas/inseridas
"""

import os
import re
import json
import tempfile
from datetime import date
from typing import Optional
from http.server import BaseHTTPRequestHandler

import fitz  # PyMuPDF
from supabase import create_client


# ============================================================
# Configuração via env vars (configure no painel do Vercel)
# ============================================================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


# ============================================================
# CONSTANTES DE EXTRAÇÃO
# ============================================================
MARCADOR_INICIO = "ASSESSORIA DE INSPEÇÃO ESCOLAR"

MARCADORES_FIM = [
    "Superintendências Regionais",
    "SUPERINTENDÊNCIAS REGIONAIS",
    "SRE de ",
    "Fundação Helena Antipoff",
    "Universidade do Estado",
    "Universidade Estadual",
    "Fundação Caio Martins",
    "Editais e Avisos",
    "EDITAIS E AVISOS",
    "Atos assinados pelo Subsecretário",
]


# ============================================================
# FUNÇÕES DE EXTRAÇÃO
# ============================================================

def extrair_texto_pdf(caminho_pdf: str) -> tuple:
    """Extrai texto do PDF usando PyMuPDF."""
    texto_completo = ""
    paginas = {}
    doc = fitz.open(caminho_pdf)
    try:
        for i, pagina in enumerate(doc, start=1):
            txt = pagina.get_text() or ""
            paginas[i] = txt
            texto_completo += f"\n[PAGINA_{i}]\n" + txt
    finally:
        doc.close()
    return texto_completo, paginas


def localizar_secao(texto_completo: str, paginas: dict) -> tuple:
    texto_upper = texto_completo.upper()
    idx_inicio = texto_upper.find(MARCADOR_INICIO)
    if idx_inicio == -1:
        return None, None

    pagina_inicio = None
    for num_pag in paginas:
        marcador_pag = f"[PAGINA_{num_pag}]"
        if marcador_pag in texto_completo[:idx_inicio]:
            pagina_inicio = num_pag

    texto_secao = texto_completo[idx_inicio:]
    fim_mais_proximo = len(texto_secao)
    for marcador_fim in MARCADORES_FIM:
        idx_fim = texto_secao.find(marcador_fim, 100)
        if idx_fim != -1 and idx_fim < fim_mais_proximo:
            fim_mais_proximo = idx_fim

    texto_secao = texto_secao[:fim_mais_proximo]
    texto_secao = re.sub(r'\[PAGINA_\d+\]', '', texto_secao)
    return texto_secao.strip(), pagina_inicio


def normalizar_texto(texto: str) -> str:
    texto = re.sub(r'-\s*\n\s*', '', texto)
    texto = re.sub(r'\s*\n\s*', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()


def quebrar_em_portarias(texto_secao: str) -> list:
    padrao = r'PORTARIA\s+N[\.\s]*[ºoº°]?\s*(\d+)\s*/\s*(\d{4})'
    matches = list(re.finditer(padrao, texto_secao, re.IGNORECASE))
    portarias = []
    for i, match in enumerate(matches):
        numero = match.group(1)
        ano = match.group(2)
        inicio = match.start()
        fim = matches[i + 1].start() if i + 1 < len(matches) else len(texto_secao)
        portarias.append({
            "numero_completo": f"{numero}/{ano}",
            "numero": int(numero),
            "ano": int(ano),
            "texto": texto_secao[inicio:fim].strip(),
        })
    return portarias


def extrair_enderecos(texto: str) -> tuple:
    match = re.search(
        r'd[ao]\s+'
        r'((?:R\.|Rua|Av\.|Avenida|Praça|Pça\.)\s+[^,]+?[\s,]+[\d/]+(?:/\d+)*(?:,\s*[\wÀ-ÿ\.\s]+?)?)'
        r'(?:,\s*em\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ\s]+?)?'
        r',?\s+para\s+(?:a\s+)?'
        r'((?:R\.|Rua|Av\.|Avenida|Praça|Pça\.)\s+[^,]+?,\s*\d+(?:/\d+)*(?:,\s*[\wÀ-ÿ\.\s]+?)?)'
        r'(?:,\s*no\s+mesmo|\.|,\s*em\s+[A-Z])',
        texto, re.IGNORECASE
    )
    if match:
        return match.group(1).strip().rstrip(','), match.group(2).strip().rstrip(',')
    return None, None


def detectar_tipo_acao(texto: str) -> str:
    txt = texto.lower()

    if "ficam revogados os atos de autorização" in txt:
        return "cessacao"
    if re.search(r'ltda[\w\s\-–\.]+para[\w\s\-–\.]+ltda', txt, re.IGNORECASE):
        return "mudanca_mantenedora"
    if "autoriza" in txt and ("funcionamento" in txt or "turma" in txt):
        return "autorizacao"
    if re.search(r'funcionamento\s+de\s+\d+\s*\([\w\s]+\)\s*turma', txt):
        return "autorizacao"
    if "mudança de denominação do logradouro" in txt or "logradouro" in txt:
        return "mudanca_logradouro"
    if "mudança do prédio" in txt or "mudança de prédio" in txt or "mudança de endereço" in txt:
        return "mudanca_predio"

    enderecos = extrair_enderecos(texto)
    if enderecos[0] and enderecos[1] and "no mesmo município" in txt:
        def num_bairro(end):
            m = re.search(r',\s*([\d/]+)\s*,\s*([\wÀ-ÿ\.\s]+)$', end)
            return (m.group(1).strip(), m.group(2).strip().lower()) if m else (None, None)
        n_a, b_a = num_bairro(enderecos[0])
        n_n, b_n = num_bairro(enderecos[1])
        if n_a and n_a == n_n and b_a == b_n:
            return "mudanca_logradouro"
        return "mudanca_predio"

    return "outros"


def extrair_sre(texto: str) -> Optional[str]:
    match = re.search(r'SRE\s*[–\-—]\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇa-zÀ-ÿ][\wÀ-ÿ\s]*?)\s*$', texto)
    return match.group(1).strip() if match else None


def extrair_escola(texto: str) -> tuple:
    padroes = [
        r'(Escola\s+(?:Municipal\s+|Estadual\s+|Particular\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ\s\-–]+?)(?:,\s*de\s+(Ensino\s+\w+(?:\s+e\s+\w+)?)\s*(?:\(([\wÀ-ÿ\s]+?)\))?|,\s*em\s+|,\s*situad)',
        r'(Centro\s+Educacional\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ\s\-]+?),\s*de\s+(Ensino\s+\w+(?:\s+e\s+\w+)?)',
        r'(Colégio\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ\s]+?)(?:,|\s+situad)',
    ]
    for padrao in padroes:
        match = re.search(padrao, texto)
        if match:
            nome = match.group(1).strip().rstrip(',').strip()
            etapa = match.group(2).strip() if match.lastindex and match.lastindex >= 2 and match.group(2) else None
            modalidade = match.group(3).strip() if match.lastindex and match.lastindex >= 3 and match.group(3) else None
            if not modalidade:
                m = re.search(r'\(((?:anos\s+iniciais|anos\s+finais|EJA|integral)[^)]*)\)', texto, re.IGNORECASE)
                if m:
                    modalidade = m.group(1).strip()
            return nome, etapa, modalidade
    return None, None, None


def extrair_municipio(texto: str) -> Optional[str]:
    match = re.search(r'em\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]+(?:\s+(?:de|do|da|das|dos)?\s*[A-ZÁÉÍÓÚÂÊÔÃÕÇ]?[\wÀ-ÿ]+){0,4}?)\.\s*SRE', texto)
    if match:
        return match.group(1).strip()
    match = re.search(r'em\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]?[\wÀ-ÿ]+){0,3}?)\s+para\s+(?:R\.|Rua|Av\.|Avenida|Praça|a\s+R\.|a\s+Av)', texto)
    if match:
        return match.group(1).strip()
    if "no mesmo município" in texto:
        match = re.search(r'em\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]?[\wÀ-ÿ]+){0,3}?)\s+para', texto)
        if match:
            return match.group(1).strip()
    matches = list(re.finditer(r'em\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]?[\wÀ-ÿ]+){0,3}?)[,\.]', texto))
    if matches:
        return matches[-1].group(1).strip()
    return None


def extrair_mantenedoras(texto: str) -> tuple:
    match = re.search(
        r'(?:de\s+|d[ao]\s+empresa\s+)([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ\s\-–\.]+?Ltda(?:\s*[–\-]\s*ME)?)\s+para\s+(?:entidade\s+|empresa\s+)?([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ\s\-–\.]+?Ltda(?:\s*[–\-]\s*ME)?)',
        texto
    )
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None, None


def extrair_bases_legais(texto: str) -> list:
    bases = []
    encontrados = set()
    padroes = [
        r'Resolução\s+(?:SEE|CEE|SEPLAG)\s+n[º\.°o]\s*[\d\.]+,\s*de\s+\d+\s+de\s+\w+\s+de\s+\d{4}',
        r'Portaria\s+(?:SEE|CEE)\s+n[º\.°o]\s*[\d\.]+,\s*de\s+\d+\s+de\s+\w+\s+de\s+\d{4}',
        r'Lei\s+(?:Complementar\s+)?n[º\.°o]\s*[\d\.]+,\s*de\s+\d+\s+de\s+\w+\s+de\s+\d{4}',
        r'Decreto\s+n[º\.°o]\s*[\d\.]+,\s*de\s+\d+\s+de\s+\w+\s+de\s+\d{4}',
        r'Resolução\s+(?:SEE|CEE|SEPLAG)\s+n[º\.°o]\s*[\d\.]+',
        r'Portaria\s+(?:SEE|CEE)\s+n[º\.°o]\s*[\d\.]+',
    ]
    for padrao in padroes:
        for match in re.finditer(padrao, texto, re.IGNORECASE):
            base = match.group(0).strip().rstrip(',.')
            chave = re.sub(r'[\s,].*$', '', base.lower())
            if chave not in encontrados:
                encontrados.add(chave)
                bases.append(base)
    return bases


def extrair_data_diario(nome_arquivo: str, texto_pdf: str) -> Optional[date]:
    meses = {
        'janeiro': 1, 'fevereiro': 2, 'março': 3, 'marco': 3, 'abril': 4,
        'maio': 5, 'junho': 6, 'julho': 7, 'agosto': 8,
        'setembro': 9, 'outubro': 10, 'novembro': 11, 'dezembro': 12,
    }

    match = re.search(r'(\d{4})[-_]?(\d{2})[-_]?(\d{2})', nome_arquivo)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass

    match = re.search(r'(\d{2})[-_](\d{2})[-_](\d{4})', nome_arquivo)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            pass

    match = re.search(
        r'(?:SEGUNDA|TER[ÇC]A|QUARTA|QUINTA|SEXTA|S[ÁA]BADO|DOMINGO)[\s\-]*FEIRA?,?\s*(\d{1,2})\s+DE\s+(\w+)\s+DE\s+(\d{4})',
        texto_pdf, re.IGNORECASE
    )
    if match:
        try:
            mes = meses.get(match.group(2).lower())
            if mes:
                return date(int(match.group(3)), mes, int(match.group(1)))
        except (ValueError, KeyError):
            pass

    match = re.search(
        r'(\d{1,2})\s+DE\s+(janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+DE\s+(\d{4})',
        texto_pdf[:10000], re.IGNORECASE
    )
    if match:
        try:
            mes = meses.get(match.group(2).lower())
            if mes:
                return date(int(match.group(3)), mes, int(match.group(1)))
        except (ValueError, KeyError):
            pass

    return None


def parsear_portaria(portaria: dict, nome_arquivo: str, pagina, data_diario) -> dict:
    texto_original = portaria["texto"]
    texto_norm = normalizar_texto(texto_original)

    nome_escola, etapa, modalidade = extrair_escola(texto_norm)
    end_ant, end_novo = extrair_enderecos(texto_norm)
    mant_ant, mant_nova = extrair_mantenedoras(texto_norm)

    return {
        "numero_portaria": portaria["numero"],
        "ano": portaria["ano"],
        "numero_completo": portaria["numero_completo"],
        "tipo_acao": detectar_tipo_acao(texto_norm),
        "escola_nome": nome_escola,
        "escola_etapa": etapa,
        "escola_modalidade": modalidade,
        "municipio": extrair_municipio(texto_norm),
        "sre": extrair_sre(texto_norm),
        "endereco_anterior": end_ant,
        "endereco_novo": end_novo,
        "mantenedora_anterior": mant_ant,
        "mantenedora_nova": mant_nova,
        "bases_legais": extrair_bases_legais(texto_norm) or None,
        "data_diario": data_diario.isoformat() if data_diario else None,
        "nome_arquivo_pdf": nome_arquivo,
        "pagina_pdf": pagina,
        "texto_completo": texto_original,
    }


# ============================================================
# HANDLER VERCEL
# ============================================================

def parse_multipart(body: bytes, content_type: str) -> dict:
    """Parser multipart/form-data simples (sem dependências extras)."""
    boundary_match = re.search(r'boundary=([^;]+)', content_type)
    if not boundary_match:
        raise ValueError("Boundary não encontrada no Content-Type")
    
    boundary = boundary_match.group(1).strip().strip('"')
    boundary_bytes = f"--{boundary}".encode()
    
    parts = body.split(boundary_bytes)
    fields = {}
    
    for part in parts:
        if not part or part == b"--\r\n" or part == b"--":
            continue
        
        # Separa headers do conteúdo (\r\n\r\n)
        if b"\r\n\r\n" not in part:
            continue
        
        headers_raw, content = part.split(b"\r\n\r\n", 1)
        # Remove o \r\n final de cada parte
        if content.endswith(b"\r\n"):
            content = content[:-2]
        
        headers_str = headers_raw.decode("utf-8", errors="ignore")
        name_match = re.search(r'name="([^"]+)"', headers_str)
        if not name_match:
            continue
        
        field_name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]+)"', headers_str)
        
        if filename_match:
            fields[field_name] = {
                "filename": filename_match.group(1),
                "content": content,
            }
        else:
            fields[field_name] = content.decode("utf-8", errors="ignore")
    
    return fields


class handler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _json_response(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def do_POST(self):
        try:
            # Validações iniciais
            if not SUPABASE_URL or not SUPABASE_KEY:
                return self._json_response(500, {
                    "error": "SUPABASE_URL e SUPABASE_SERVICE_KEY não configuradas"
                })

            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data"):
                return self._json_response(400, {"error": "Esperado multipart/form-data"})

            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                return self._json_response(400, {"error": "Body vazio"})

            # Lê o body
            body = self.rfile.read(content_length)
            fields = parse_multipart(body, content_type)

            if "pdf" not in fields or not isinstance(fields["pdf"], dict):
                return self._json_response(400, {"error": "Campo 'pdf' não encontrado"})

            pdf_data = fields["pdf"]["content"]
            nome_arquivo = fields["pdf"]["filename"]

            # Salva temp file (PyMuPDF precisa de path)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(pdf_data)
                tmp_path = tmp.name

            try:
                texto, paginas = extrair_texto_pdf(tmp_path)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            data_diario = extrair_data_diario(nome_arquivo, texto)
            texto_secao, pagina_secao = localizar_secao(texto, paginas)

            if not texto_secao:
                return self._json_response(200, {
                    "arquivo": nome_arquivo,
                    "secao_encontrada": False,
                    "portarias_extraidas": 0,
                    "portarias_inseridas": 0,
                    "portarias": [],
                    "mensagem": "Seção 'Assessoria de Inspeção Escolar' não encontrada neste PDF",
                })

            portarias_brutas = quebrar_em_portarias(texto_secao)
            portarias = [parsear_portaria(p, nome_arquivo, pagina_secao, data_diario) for p in portarias_brutas]

            # Insere no Supabase
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            inseridas = 0
            duplicadas = 0
            erros = []

            for p in portarias:
                try:
                    supabase.table("portarias_inspecao").insert(p).execute()
                    inseridas += 1
                except Exception as e:
                    msg = str(e)
                    if "duplicate" in msg.lower() or "unique" in msg.lower():
                        duplicadas += 1
                    else:
                        erros.append({"portaria": p["numero_completo"], "erro": msg[:200]})

            return self._json_response(200, {
                "arquivo": nome_arquivo,
                "secao_encontrada": True,
                "pagina": pagina_secao,
                "data_diario": data_diario.isoformat() if data_diario else None,
                "portarias_extraidas": len(portarias),
                "portarias_inseridas": inseridas,
                "portarias_duplicadas": duplicadas,
                "erros": erros,
                "portarias": [
                    {
                        "numero_completo": p["numero_completo"],
                        "tipo_acao": p["tipo_acao"],
                        "escola_nome": p["escola_nome"],
                        "escola_etapa": p["escola_etapa"],
                        "municipio": p["municipio"],
                        "sre": p["sre"],
                    }
                    for p in portarias
                ],
            })

        except Exception as e:
            import traceback
            return self._json_response(500, {
                "error": str(e),
                "trace": traceback.format_exc()[:1000],
            })
