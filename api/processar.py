"""
Vercel Serverless Function - Processa PDF do Supabase Storage
e insere portarias da Assessoria de Inspeção Escolar no banco.

Endpoint: POST /api/processar
Recebe:   JSON com {"storage_path": "diarios/arquivo.pdf"}
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


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
BUCKET_NAME = "diarios"


MARCADORES_INICIO = [
    "ASSESSORIA DE INSPEÇÃO ESCOLAR",
    "SUPERINTENDÊNCIA DE REGULAÇÃO E INSPEÇÃO ESCOLAR",
    "SUPERINTENDENCIA DE REGULACAO E INSPECAO ESCOLAR",
    "ATOS ASSINADOS PELA SUBSECRETÁRIA DE ARTICULAÇÃO EDUCACIONAL",
    "ATOS ASSINADOS PELA SUBSECRETARIA DE ARTICULACAO EDUCACIONAL",
]

MARCADORES_FIM = [
    "Superintendências Regionais de Ensino - SRE",
    "SUPERINTENDÊNCIAS REGIONAIS DE ENSINO",
    "SRE de Almenara",
    "SRE de Araçuaí",
    "SRE de Barbacena",
    "SRE de Caratinga",
    "SRE de Carangola",
    "SRE de Caxambu",
    "SRE de Conselheiro Lafaiete",
    "SRE de Coronel Fabriciano",
    "SRE de Curvelo",
    "SRE de Diamantina",
    "SRE de Divinópolis",
    "SRE de Governador Valadares",
    "SRE de Guanhães",
    "SRE de Itajubá",
    "SRE de Ituiutaba",
    "SRE de Januária",
    "SRE de Juiz de Fora",
    "SRE de Leopoldina",
    "SRE de Manhuaçu",
    "SRE de Metropolitana",
    "SRE de Monte Carmelo",
    "SRE de Montes Claros",
    "SRE de Muriaé",
    "SRE de Nova Era",
    "SRE de Ouro Preto",
    "SRE de Pará de Minas",
    "SRE de Paracatu",
    "SRE de Passos",
    "SRE de Patos de Minas",
    "SRE de Patrocínio",
    "SRE de Pirapora",
    "SRE de Poços de Caldas",
    "SRE de Ponte Nova",
    "SRE de Pouso Alegre",
    "SRE de São João Del Rei",
    "SRE de São Sebastião do Paraíso",
    "SRE de Sete Lagoas",
    "SRE de Teófilo Otoni",
    "SRE de Ubá",
    "SRE de Uberaba",
    "SRE de Uberlândia",
    "SRE de Unaí",
    "Fundação Helena Antipoff",
    "Universidade do Estado",
    "Universidade Estadual",
    "Fundação Caio Martins",
    "Editais e Avisos",
    "EDITAIS E AVISOS",
]


def extrair_texto_pdf(caminho_pdf: str) -> tuple:
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
    """Localiza a seção de Inspeção Escolar (legacy fallback).
    
    Mantida para compatibilidade, mas a estratégia principal agora é
    identificar portarias diretamente pelo padrão (ver quebrar_em_portarias).
    Esta função apenas determina a página onde aparece o cabeçalho, se houver.
    """
    texto_upper = texto_completo.upper()
    idx_inicio = -1
    for marcador in MARCADORES_INICIO:
        idx = texto_upper.find(marcador)
        if idx != -1 and (idx_inicio == -1 or idx < idx_inicio):
            idx_inicio = idx
    
    pagina_inicio = None
    if idx_inicio != -1:
        for num_pag in paginas:
            marcador_pag = f"[PAGINA_{num_pag}]"
            if marcador_pag in texto_completo[:idx_inicio]:
                pagina_inicio = num_pag
    
    # Retorna o texto inteiro (sem marcadores de página) - quebrar_em_portarias
    # vai fazer o filtro pelo padrão característico das portarias.
    texto_limpo = re.sub(r'\[PAGINA_\d+\]', '', texto_completo)
    return texto_limpo.strip(), pagina_inicio


def normalizar_texto(texto: str) -> str:
    texto = re.sub(r'-\s*\n\s*', '', texto)
    texto = re.sub(r'\s*\n\s*', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()


def quebrar_em_portarias(texto_secao: str) -> list:
    """Identifica portarias da Assessoria/Superintendência de Inspeção Escolar.
    
    Estratégia: encontra todas as 'PORTARIA SEE N.º X/AAAA' (ou 'PORTARIA N.º') 
    no texto e filtra pelas que têm o padrão característico de portarias 
    de inspeção escolar (PROCESSO N.º + Resolução SEE/artigo 13 + termina com SRE –).
    
    Isso é necessário porque o PyMuPDF extrai texto de PDFs em colunas em ordem
    espacial, então portarias podem aparecer em ordem diferente da visual e
    podem estar separadas dos cabeçalhos da seção.
    """
    padrao = r'PORTARIA(?:\s+SEE)?\s+N[\.\s]*[ºoº°]?\s*(\d+)\s*/\s*(\d{4})'
    matches = list(re.finditer(padrao, texto_secao, re.IGNORECASE))
    
    portarias = []
    vistos = set()  # Para deduplicar
    
    for i, match in enumerate(matches):
        numero = match.group(1)
        ano = match.group(2)
        chave = f"{numero}/{ano}"
        
        if chave in vistos:
            continue
        
        inicio = match.start()
        fim = matches[i + 1].start() if i + 1 < len(matches) else len(texto_secao)
        
        # Limita o tamanho de uma portaria para 5000 chars (evita capturar lixo)
        if fim - inicio > 5000:
            fim = inicio + 5000
        
        texto_portaria = texto_secao[inicio:fim].strip()
        
        # FILTRO: portarias de inspeção escolar têm:
        #   1. "PROCESSO N." nos próximos 300 chars
        #   2. Mencionam "Resolução SEE" ou "artigo 13"
        #   3. Terminam com "SRE –" ou "SRE -"
        cabecalho = texto_portaria[:400]
        tem_processo = bool(re.search(r'PROCESSO\s+N[\.\s]*[ºoº°]?', cabecalho, re.IGNORECASE))
        tem_resolucao = bool(re.search(r'Resolu[çc][ãa]o\s+SEE|artigo\s+13|art\.?\s*13', cabecalho, re.IGNORECASE))
        tem_sre = bool(re.search(r'SRE\s*[–\-]\s*\w+', texto_portaria))
        
        # Aceita se tiver pelo menos 2 dos 3 sinais
        sinais = sum([tem_processo, tem_resolucao, tem_sre])
        if sinais < 2:
            continue
        
        vistos.add(chave)
        portarias.append({
            "numero_completo": chave,
            "numero": int(numero),
            "ano": int(ano),
            "texto": texto_portaria,
        })
    
    # Ordenar por número
    portarias.sort(key=lambda p: (p["ano"], p["numero"]))
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
            if not SUPABASE_URL or not SUPABASE_KEY:
                return self._json_response(500, {
                    "error": "SUPABASE_URL e SUPABASE_SERVICE_KEY não configuradas"
                })

            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                return self._json_response(400, {"error": "Body vazio"})

            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return self._json_response(400, {"error": "JSON inválido"})

            storage_path = payload.get("storage_path")
            nome_arquivo = payload.get("nome_arquivo", storage_path)

            if not storage_path:
                return self._json_response(400, {"error": "storage_path é obrigatório"})

            # Conecta no Supabase e baixa o arquivo do Storage
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

            try:
                pdf_bytes = supabase.storage.from_(BUCKET_NAME).download(storage_path)
            except Exception as e:
                return self._json_response(500, {
                    "error": f"Erro ao baixar do Storage: {str(e)[:200]}"
                })

            # Salva temp file (PyMuPDF precisa de path)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(pdf_bytes)
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

            # texto_secao agora retorna o texto completo do PDF.
            # A filtragem de portarias é feita por padrão em quebrar_em_portarias.
            portarias_brutas = quebrar_em_portarias(texto_secao or "")
            
            if not portarias_brutas:
                return self._json_response(200, {
                    "arquivo": nome_arquivo,
                    "secao_encontrada": False,
                    "portarias_extraidas": 0,
                    "portarias_inseridas": 0,
                    "portarias": [],
                    "mensagem": "Nenhuma portaria de Inspeção Escolar encontrada neste PDF",
                })
            
            portarias = [parsear_portaria(p, nome_arquivo, pagina_secao, data_diario) for p in portarias_brutas]

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
                        "endereco_anterior": p["endereco_anterior"],
                        "endereco_novo": p["endereco_novo"],
                        "mantenedora_anterior": p["mantenedora_anterior"],
                        "mantenedora_nova": p["mantenedora_nova"],
                        "texto_completo": p["texto_completo"],
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
