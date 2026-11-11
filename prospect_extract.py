"""
QuickScanMenu – Extração de leads + WhatsApp + Página de Contato
Fluxo: Bolt Scraper → SQLite (com migração automática) → CSV
"""
import os
import re
import sqlite3
import argparse
import math
from pathlib import Path
from urllib.parse import quote

import requests
import pandas as pd
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()

# =========================================================
# CONFIGURAÇÕES
# =========================================================
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "prospect.db"
BOLT_API_KEY = os.getenv("BOLTSCRAPER_API_KEY")

NICHOS = {
    "1": {"key": "restaurantes", "label": "Restaurantes", "search": "restaurante", "mensagem": "Olá! Tudo bem? Vi que vocês têm o {nome} em {cidade}. Meu nome é [SEU NOME], da QuickScanMenu. Trabalho com cardápio digital e PDV para restaurantes. Posso te mostrar como funciona em 2 minutos?"},
    "2": {"key": "padarias", "label": "Padarias", "search": "padaria", "mensagem": "Olá! Tudo bem? Vi o {nome} em {cidade}. Sou [SEU NOME], da QuickScanMenu. Ajudamos padarias com PDV, modo balcão e gestão de mesas. Posso te mandar um exemplo de como fica?"},
    "3": {"key": "pizzarias", "label": "Pizzarias", "search": "pizzaria", "mensagem": "Olá! Tudo bem? Vi a {nome} em {cidade}. Sou [SEU NOME], da QuickScanMenu. Trabalho com PDV e cardápio digital para pizzarias (controle de sabores e delivery). Posso te mostrar?"},
    "4": {"key": "hamburguerias", "label": "Hamburguerias", "search": "hamburgueria", "mensagem": "Olá! Tudo bem? Vi a {nome} em {cidade}. Sou [SEU NOME], da QuickScanMenu. Ajudamos hamburguerias com PDV, combos e delivery. Posso te mostrar como funciona?"},
    "5": {"key": "lanchonetes", "label": "Lanchonetes", "search": "lanchonete", "mensagem": "Olá! Tudo bem? Vi a {nome} em {cidade}. Sou [SEU NOME], da QuickScanMenu. Trabalho com PDV e gestão para lanchonetes. Posso te mandar um exemplo?"},
    "6": {"key": "bares", "label": "Bares", "search": "bar", "mensagem": "Olá! Tudo bem? Vi o {nome} em {cidade}. Sou [SEU NOME], da QuickScanMenu. Ajudamos bares com comandas, gestão de mesas e estoque. Posso te mostrar?"},
    "7": {"key": "casas_noturnas", "label": "Casas Noturnas", "search": "casa noturna", "mensagem": "Olá! Tudo bem? Vi a {nome} em {cidade}. Sou [SEU NOME], da QuickScanMenu. Trabalho com PDV, comandas e controle de ingressos para casas noturnas. Posso te mostrar?"},
    "8": {"key": "depositos_bebidas", "label": "Depósitos de Bebidas", "search": "depósito de bebidas", "mensagem": "Olá! Tudo bem? Vi o {nome} em {cidade}. Sou [SEU NOME], da QuickScanMenu. Ajudamos depósitos com PDV, estoque e entregas. Posso te mostrar como funciona?"},
    "9": {"key": "acaiterias", "label": "Açaiterias", "search": "açaiteria", "mensagem": "Olá! Tudo bem? Vi a {nome} em {cidade}. Sou [SEU NOME], da QuickScanMenu. Trabalho com PDV, adicionais e delivery para açaiterias. Posso te mostrar?"},
}

# =========================================================
# BANCO DE DADOS (Com Migração Automática)
# =========================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    
    # Cria a tabela se não existir
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT,
            whatsapp TEXT,
            telefone TEXT,
            endereco TEXT,
            cidade TEXT,
            nicho TEXT,
            site TEXT,
            link_whatsapp TEXT,
            pagina_contato TEXT,
            contatado_em TEXT,
            criado_em TEXT DEFAULT (datetime('now')),
            UNIQUE(nome, cidade)
        )
    """)
    
    # MIGRAÇÃO: Verifica se a coluna existe. Se não, cria na hora.
    cursor = conn.execute("PRAGMA table_info(leads)")
    colunas = [coluna[1] for coluna in cursor.fetchall()]
    
    if "pagina_contato" not in colunas:
        print("[DB] Criando coluna 'pagina_contato' no banco de dados...")
        conn.execute("ALTER TABLE leads ADD COLUMN pagina_contato TEXT")
        conn.commit()
        
    if "contatado_em" not in colunas:
        conn.execute("ALTER TABLE leads ADD COLUMN contatado_em TEXT")
        conn.commit()

    return conn

def salvar_lead(conn, lead, cidade, nicho):
    try:
        # Usando Named Placeholders (evita erro de contagem de vírgulas)
        dados = {
            "nome": (lead.get("nome") or "").strip(),
            "email": (lead.get("email") or "").strip(),
            "whatsapp": (lead.get("whatsapp") or "").strip(),
            "telefone": (lead.get("telefone") or "").strip(),
            "endereco": (lead.get("endereco") or "").strip(),
            "cidade": cidade,
            "nicho": nicho,
            "site": (lead.get("site") or "").strip(),
            "link_whatsapp": (lead.get("link_whatsapp") or "").strip(),
            "pagina_contato": (lead.get("pagina_contato") or "").strip(),
        }
        
        conn.execute("""
            INSERT INTO leads (nome, email, whatsapp, telefone, endereco, cidade, nicho, site, link_whatsapp, pagina_contato)
            VALUES (:nome, :email, :whatsapp, :telefone, :endereco, :cidade, :nicho, :site, :link_whatsapp, :pagina_contato)
        """, dados)
        
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False # Duplicado
    except Exception as e:
        print(f"[Erro DB] Falha ao salvar {lead.get('nome')}: {e}")
        return False

# =========================================================
# EXTRAÇÃO DO SITE
# =========================================================
def extrair_contatos_do_site(site_url: str):
    whatsapp, email, pagina_contato = "", "", ""
    if not site_url:
        return whatsapp, email, pagina_contato
        
    try:
        resp = requests.get(site_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        html = resp.text
        site_base = site_url.rstrip('/')
        
        def buscar_email(texto_html):
            emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', texto_html)
            for e in emails:
                e_lower = e.lower()
                # Filtra lixos
                if not any(x in e_lower for x in [".png", ".jpg", ".jpeg", ".gif", ".svg", "example.com", "sentry.io", "wixpress.com", "domain.com"]):
                    return e
            return ""

        # 1. WhatsApp
        padroes_wa = [r"wa\.me/(\d{10,15})", r"api\.whatsapp\.com/send\?phone=(\d{10,15})"]
        for padrao in padroes_wa:
            match = re.search(padrao, html)
            if match:
                whatsapp = match.group(1)
                break
                
        # 2. E-mail inicial
        email = buscar_email(html)
        
        # 3. Página de Contato
        if not email:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                texto_link = a.text.lower()
                href_link = a["href"].lower()
                
                if any(termo in texto_link or termo in href_link for termo in ["contato", "fale-conosco", "faleconosco", "contact", "atendimento", "suporte"]):
                    if href_link.startswith("http"):
                        pagina_contato = a["href"]
                    elif href_link.startswith("#") or "javascript" in href_link:
                        continue
                    else:
                        pagina_contato = f"{site_base}/{a['href'].lstrip('/')}"
                    break
            
            if pagina_contato:
                try:
                    resp_contato = requests.get(pagina_contato, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
                    if resp_contato.status_code == 200:
                        email = buscar_email(resp_contato.text)
                except Exception:
                    pass
                    
    except Exception:
        pass
        
    return whatsapp, email, pagina_contato

def normalizar_telefone_br(phone: str) -> str:
    if not phone: return ""
    digits = re.sub(r"\D", "", phone)
    if not digits: return ""
    if digits.startswith("55") and 12 <= len(digits) <= 13: return digits
    if 10 <= len(digits) <= 11: return "55" + digits
    return digits

def gerar_link_wa(whatsapp: str, mensagem: str) -> str:
    numero = normalizar_telefone_br(whatsapp)
    if not numero: return ""
    return f"https://wa.me/{numero}?text={quote(mensagem)}"

# =========================================================
# SCRAPING
# =========================================================
def scrape_leads(tipo_busca: str, cidade: str, mensagem_template: str, max_resultados: int = 50):
    if not BOLT_API_KEY:
        print("[Erro] BOLTSCRAPER_API_KEY não configurada no .env")
        return []

    url = "https://boltscraper.com/api/v1/maps/search"
    headers = {"Authorization": f"Bearer {BOLT_API_KEY}", "Content-Type": "application/json"}
    
    cidade_limpa = cidade.replace(" - ", " ").replace("-", " ")
    query = f"{tipo_busca} {cidade_limpa}"
    paginas_necessarias = math.ceil(max_resultados / 20)
    
    print(f"[Scraping] Alvo: {max_resultados} leads. Buscando {paginas_necessarias} página(s)...")

    leads_brutos = []
    
    for pagina in range(1, paginas_necessarias + 1):
        payload = {"query": query, "page": pagina}
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=45)
            if response.status_code != 200:
                print(f"[Erro API] Status {response.status_code} na página {pagina}: {response.text}")
                break
                
            data = response.json()
            results_pagina = data.get("data") or data.get("results") or []
            if not results_pagina and isinstance(data, list):
                results_pagina = data

            print(f"  -> Página {pagina}: {len(results_pagina)} resultados encontrados.")
            
            if not results_pagina:
                break 
                
            leads_brutos.extend(results_pagina)
            if len(leads_brutos) >= max_resultados:
                break
                
        except Exception as e:
            print(f"[Erro de Conexão] na página {pagina}: {e}")
            break

    resultados_finais = leads_brutos[:max_resultados]
    print(f"[Scraping] Total final para processar: {len(resultados_finais)} leads.")
        
    leads = []
    for place in resultados_finais:
        nome = place.get("Name") or place.get("name") or place.get("title") or ""
        site = place.get("Website") or place.get("website") or ""
        telefone = place.get("Phone") or place.get("phone") or ""
        endereco = place.get("Full Address") or place.get("address") or place.get("full_address") or ""
        
        if not nome:
            continue
            
        whatsapp, email, pagina_contato = extrair_contatos_do_site(site)

        if not whatsapp and telefone:
            digits = re.sub(r"\D", "", telefone)
            if len(digits) >= 10 and len(digits) <= 11:
                whatsapp = telefone

        mensagem = mensagem_template.format(nome=nome, cidade=cidade)
        link_wa = gerar_link_wa(whatsapp, mensagem) if whatsapp else ""

        lead = {
            "nome": nome, "email": email, "whatsapp": whatsapp, "telefone": telefone,
            "endereco": endereco, "site": site, "link_whatsapp": link_wa, "pagina_contato": pagina_contato,
        }
        leads.append(lead)
        
    return leads

# =========================================================
# FLUXO PRINCIPAL
# =========================================================
def processar(nicho: dict, cidade: str, max_resultados: int):
    conn = init_db()
    leads = scrape_leads(nicho["search"], cidade, nicho["mensagem"], max_resultados)
    
    if not leads:
        print("Nenhum lead coletado.")
        conn.close()
        return

    salvos = 0
    duplicados = 0
    com_whatsapp = 0
    com_email = 0
    com_formulario = 0
    
    for lead in leads:
        if salvar_lead(conn, lead, cidade, nicho["key"]):
            salvos += 1
            if lead.get("whatsapp"): com_whatsapp += 1
            if lead.get("email"): com_email += 1
            if lead.get("pagina_contato"): com_formulario += 1
        else:
            duplicados += 1

    print(f"\n[Resumo] {salvos} leads novos salvos | {duplicados} duplicados ignorados")
    print(f"          {com_whatsapp} com WhatsApp | {com_email} com E-mail | {com_formulario} com Link de Formulário")
    conn.close()

def exportar_csv():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT nome, whatsapp, email, telefone, endereco, cidade, nicho, site, link_whatsapp, pagina_contato FROM leads ORDER BY id",
        conn,
    )
    conn.close()
    
    caminho = BASE_DIR / "leads_whatsapp.csv"
    df.to_csv(caminho, index=False, encoding="utf-8-sig")
    print(f"\n[Export] {len(df)} leads exportados para {caminho.name}")

def menu_interativo():
    print("\n=== Selecione o nicho ===")
    for k, v in NICHOS.items():
        print(f"  [{k:>2}] {v['label']}")
    while True:
        escolha = input("\nNúmero do nicho: ").strip()
        if escolha in NICHOS:
            return NICHOS[escolha]
        print("  Opção inválida.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nicho", help="Termo de busca")
    parser.add_argument("--cidade", help="Cidade")
    parser.add_argument("--max", type=int, default=20)
    args = parser.parse_args()

    if args.nicho:
        nicho_digitado = args.nicho.lower().strip()
        matches = [v for v in NICHOS.values() if v["key"] == nicho_digitado]
        if matches:
            nicho = matches[0]
        else:
            print(f"[Aviso] Nicho '{nicho_digitado}' não no menu. Usando mensagem genérica...")
            nicho = {
                "key": nicho_digitado, "label": nicho_digitado.capitalize(), "search": nicho_digitado,
                "mensagem": f"Olá! Tudo bem? Vi o {{nome}} em {{cidade}}. Sou [SEU NOME], da QuickScanMenu. Trabalho com PDV e cardápio digital para {nicho_digitado}. Posso te mostrar como funciona?"
            }
    else:
        nicho = menu_interativo()

    cidade = args.cidade or input("Cidade: ").strip()
    if not cidade:
        raise SystemExit(1)

    processar(nicho, cidade, args.max)
    exportar_csv()