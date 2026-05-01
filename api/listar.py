"""Endpoint /api/listar - retorna portarias já salvas no banco."""
import json
import os
from http.server import BaseHTTPRequestHandler

from supabase import create_client


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


class handler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _json_response(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        try:
            if not SUPABASE_URL or not SUPABASE_KEY:
                return self._json_response(500, {
                    "error": "Variáveis de ambiente do Supabase não configuradas"
                })

            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

            # Busca tudo, ordenado pela mais recente primeiro
            result = (
                supabase
                .table("portarias_inspecao")
                .select(
                    "numero_completo,tipo_acao,escola_nome,escola_etapa,"
                    "municipio,sre,endereco_anterior,endereco_novo,"
                    "mantenedora_anterior,mantenedora_nova,texto_completo,"
                    "nome_arquivo_pdf,data_diario,extraido_em,ano,numero_portaria"
                )
                .order("extraido_em", desc=True)
                .limit(2000)
                .execute()
            )

            portarias = result.data or []

            # Renomeia nome_arquivo_pdf → arquivo (consistente com o frontend)
            for p in portarias:
                p["arquivo"] = p.pop("nome_arquivo_pdf", None)

            return self._json_response(200, {
                "total": len(portarias),
                "portarias": portarias,
            })

        except Exception as e:
            import traceback
            return self._json_response(500, {
                "error": str(e)[:300],
                "trace": traceback.format_exc()[:500],
            })
