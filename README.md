# Extrator de Portarias — Web App

Interface web hospedada no **Vercel** que permite arrastar PDFs do Diário Oficial de MG e extrair automaticamente as portarias da seção "Assessoria de Inspeção Escolar" para o Supabase.

## O que tem aqui

```
vercel_app/
├── api/
│   └── processar.py        ← Serverless function (Python) que processa o PDF
├── public/
│   └── index.html          ← Frontend (drag & drop, upload múltiplo, resultados)
├── vercel.json             ← Config do Vercel (rotas, timeout)
├── requirements.txt        ← Dependências Python
└── .gitignore
```

## Como o sistema funciona

1. Você acessa a URL do seu app no Vercel
2. Arrasta um ou vários PDFs do diário (ou clica para selecionar)
3. Clica em "Processar Diários"
4. Para cada PDF, o frontend faz `POST /api/processar` com o arquivo
5. A serverless function:
   - Extrai texto do PDF com PyMuPDF
   - Localiza a seção "Assessoria de Inspeção Escolar"
   - Quebra em portarias individuais
   - Estrutura cada uma (tipo, escola, município, SRE, endereços, etc.)
   - Insere no Supabase (ignora duplicatas)
6. Frontend mostra estatísticas e lista das portarias extraídas

---

## Deploy passo a passo

### Pré-requisitos

- Conta no [Vercel](https://vercel.com) (gratuita)
- Conta no [GitHub](https://github.com) (gratuita) — opcional, mas recomendado
- A `SUPABASE_SERVICE_KEY` do projeto Pacto_famulário

### Opção A: Deploy via GitHub (recomendado)

**1. Criar repositório no GitHub**

```bash
cd vercel_app
git init
git add .
git commit -m "Initial commit"
gh repo create extrator-portarias --private --source=. --push
```

(Ou, sem o `gh` CLI: crie o repo manualmente em github.com e faça push.)

**2. Conectar Vercel ao repositório**

1. Vá em [vercel.com/new](https://vercel.com/new)
2. Clique em "Import Git Repository"
3. Selecione o repo `extrator-portarias`
4. **Antes de clicar em Deploy**, configure as variáveis de ambiente:
   - `SUPABASE_URL` = `https://vczwlfcfrlvyzabwievl.supabase.co`
   - `SUPABASE_SERVICE_KEY` = (sua chave service_role, pega no Supabase Dashboard → Project Settings → API)
5. Clique em **Deploy**

**3. Pegar a URL**

Após ~2 minutos, o Vercel mostra a URL (ex: `https://extrator-portarias.vercel.app`). Acesse e use!

### Opção B: Deploy via CLI (sem GitHub)

```bash
npm install -g vercel
cd vercel_app
vercel
```

O CLI vai perguntar:
- `Set up and deploy?` → **Y**
- `Which scope?` → escolha sua conta
- `Link to existing project?` → **N**
- `Project name?` → `extrator-portarias`
- `In which directory?` → **./** (Enter)
- `Want to modify these settings?` → **N**

Depois adicione as env vars:
```bash
vercel env add SUPABASE_URL
# cole: https://vczwlfcfrlvyzabwievl.supabase.co
vercel env add SUPABASE_SERVICE_KEY
# cole: sua chave service_role
```

E faça redeploy:
```bash
vercel --prod
```

---

## Onde pegar a SUPABASE_SERVICE_KEY

1. Acesse [supabase.com/dashboard](https://supabase.com/dashboard)
2. Selecione o projeto **Pacto_famulário**
3. Vá em **Project Settings** (ícone de engrenagem) → **API**
4. Procure pela seção **Project API keys**
5. Copie a chave `service_role` (a `secret`, não a `anon`)

⚠️ **Nunca** exponha essa chave no frontend nem commite no git. Ela tem acesso total ao banco. Por isso ela vai como variável de ambiente da serverless function (server-side).

---

## Custos

Tudo na free tier:

- **Vercel Hobby**: 100GB-h de execução/mês, deploys ilimitados
- **Supabase Free**: 500MB de banco, 50.000 invocações de Edge Function/mês
- **Total**: $0/mês para uso pessoal

Limites pra ficar de olho:
- Função tem **timeout de 60s** (configurado em `vercel.json`)
- PDF máximo: **50MB** (limite do frontend; ajustável)

---

## Testando localmente antes de fazer deploy

```bash
npm install -g vercel
cd vercel_app
vercel dev
```

Vai abrir em `http://localhost:3000`. Configure as env vars no `.env.local`:

```
SUPABASE_URL=https://vczwlfcfrlvyzabwievl.supabase.co
SUPABASE_SERVICE_KEY=sua_chave_aqui
```

---

## Resolução de problemas

**"Seção não encontrada" em todos os PDFs**
→ O PDF pode ter texto em imagem. Solução: rode OCR antes (Adobe Acrobat, Tesseract, etc).

**Timeout (>60s)**
→ PDFs muito grandes ou muitas portarias. Aumente `maxDuration` no `vercel.json` (até 60s no plano Hobby, 300s no Pro).

**Erro de import do PyMuPDF no Vercel**
→ Vercel só suporta versões específicas. Confira que `requirements.txt` está com `PyMuPDF==1.24.10`.

**CORS errors no navegador**
→ Já tratado no `processar.py` (headers `Access-Control-Allow-*`). Se persistir, confira que está acessando pela URL do Vercel, não direto pelo arquivo.

---

## Acessando os dados depois

Os dados ficam na tabela `portarias_inspecao` do Supabase. Você pode:

- **Visualizar**: Supabase Dashboard → Table Editor → portarias_inspecao
- **Consultar via SQL**: Dashboard → SQL Editor
- **Exportar**: Dashboard → Table Editor → ⋯ → Download as CSV
- **Acessar via API**: usando a SDK do Supabase em qualquer aplicação

Exemplo de consultas úteis:

```sql
-- Top 10 SREs com mais portarias
SELECT sre, COUNT(*) as total
FROM portarias_inspecao
GROUP BY sre
ORDER BY total DESC
LIMIT 10;

-- Escolas que cessaram atividades este ano
SELECT escola_nome, municipio, sre, data_diario
FROM portarias_inspecao
WHERE tipo_acao = 'cessacao'
  AND data_diario >= '2025-01-01'
ORDER BY data_diario DESC;
```
