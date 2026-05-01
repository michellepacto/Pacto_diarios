"""
Vercel Serverless Function — Lista IDs de portarias sem escola_nome.

Endpoint: GET /api/listar_sem_nome
Retorna:  JSON com lista de portarias que precisam de análise por IA
         {"portarias": [{"id": 1, "numero_completo": "..."}, ...]}

Usado pelo botão "Buscar nomes com IA" para saber em quais portarias rodar.
"""

import os
import json
from http.server import BaseHTTPRequestHandler

from supabase import create_client


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


class handler(BaseHTTPRequestHandler):

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
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

    def do_GET(self):
        if not SUPABASE_URL or not SUPABASE_KEY:
            self._json(500, {"erro": "Supabase não configurado"})
            return

        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

            # Buscar portarias sem escola_nome E sem escola_nome_ia (ainda não analisadas)
            # Usa .or_ pra cobrir tanto NULL quanto string vazia
            result = (
                supabase.table("portarias_inspecao")
                .select("id,numero_completo,data_diario")
                .is_("escola_nome", "null")
                .is_("escola_nome_ia", "null")
                .order("data_diario", desc=True)
                .limit(2000)
                .execute()
            )

            portarias_null = result.data or []

            # Cobrir também as com string vazia (extração antiga colocava "")
            result2 = (
                supabase.table("portarias_inspecao")
                .select("id,numero_completo,data_diario,escola_nome")
                .eq("escola_nome", "")
                .is_("escola_nome_ia", "null")
                .order("data_diario", desc=True)
                .limit(2000)
                .execute()
            )

            portarias_vazio = result2.data or []

            # Juntar e deduplicar por id
            todas = {p["id"]: p for p in portarias_null}
            for p in portarias_vazio:
                todas[p["id"]] = p

            self._json(200, {
                "total": len(todas),
                "portarias": list(todas.values()),
            })

        except Exception as e:
            self._json(500, {"erro": str(e)})
