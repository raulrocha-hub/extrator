"""
QuickScanMenu – Extração de leads + geração de links WhatsApp
Fluxo: Bolt Scraper (API REST) → SQLite → CSV com links wa.me personalizados
"""
import os
import re
import sqlite3
import argparse
from pathlib import Path
from urllib.parse import quote

import requests
import pandas as pd
from dotenv import load_dotenv

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
    "10": {"key": "site_app_cardapio", "label": "Site + App + Cardápio", "search": "restaurante", "mensagem": "Olá! Tudo bem? Vi o {nome} em {cidade}. Sou [SEU NOME], da QuickScanMenu. Trabalho com site, app e cardápio digital integrados para restaurantes. Posso te mostrar um exemplo?"},
}

# =========================================================
# BANCO DE DADOS
# =========================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    # MUDANÇA AQUI: UNIQUE agora é (nome, cidade) para evitar duplicatas na mesma cidade
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
            contatado_em TEXT,
            criado_em TEXT DEFAULT (datetime('now')),
            UNIQUE(nome, cidade)
        )
    """)
    conn.commit()
    return conn

def salvar_lead(conn, lead, cidade, nicho):
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO leads (nome, email, whatsapp, telefone, endereco, cidade, nicho, site, link_whatsapp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                (lead.get("nome") or "").strip(),
                (lead.get("email") or "").strip(),
                (lead.get("whatsapp") or "").strip(),
                (lead.get("telefone") or "").strip(),
                (lead.get("endereco") or "").strip(),
                cidade,
                nicho,
                (lead.get("site") or "").strip(),
                (lead.get("link_whatsapp") or "").strip(),
            ),
        )
        conn.commit()
        return True # Sucesso
    except sqlite3.IntegrityError:
        return False # Duplicado (já existe com esse nome nessa cidade)

# =========================================================
# EXTRAÇÃO DO SITE (WhatsApp + E-mail)
# =========================================================
def extrair_contatos_do_site(site_url: str):
    """Visita o site, extrai o primeiro WhatsApp e o primeiro E-mail válido."""
    whatsapp, email = "", ""
    if not site_url:
        return whatsapp, email
        
    try:
        # Timeout curto para não travar
        resp = requests.get(site_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        html = resp.text
        
        # 1. Extração de WhatsApp
        padroes_wa = [
            r"wa\.me/(\d{10,15})",
            r"api\.whatsapp\.com/send\?phone=(\d{10,15})",
            r"web\.whatsapp\.com/send\?phone=(\d{10,15})",
        ]
        for padrao in padroes_wa:
            match = re.search(padrao, html)
            if match:
                whatsapp = match.group(1)
                break
                
        # 2. Extração de E-mail
        # Busca qualquer coisa que pareça e-mail
        emails_encontrados = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
        if emails_encontrados:
            # Filtra lixos comuns (imagens, bibliotecas, exemplos)
            for e in emails_encontrados:
                e_lower = e.lower()
                if not any(x in e_lower for x in [".png", ".jpg", ".jpeg", ".gif", ".svg", "example.com", "sentry.io", "wixpress.com"]):
                    email = e
                    break # Pega o primeiro válido
                    
    except Exception:
        pass
        
    return whatsapp, email

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
# SCRAPING (CHAMADA REST DIRETA)
# =========================================================
def scrape_leads(tipo_busca: str, cidade: str, mensagem_template: str, max_resultados: int = 50):
    if not BOLT_API_KEY:
        print("[Erro] BOLTSCRAPER_API_KEY não configurada no .env")
        return []

    url = "https://boltscraper.com/api/v1/maps/search"
    headers = {
        "Authorization": f"Bearer {BOLT_API_KEY}",
        "Content-Type": "application/json"
    }
    
    cidade_limpa = cidade.replace(" - ", " ").replace("-", " ")
    query = f"{tipo_busca} {cidade_limpa}"
    payload = {"query": query, "limit": max_resultados}

    print(f"[Scraping] Chamando API do Bolt para: '{query}'")

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=45)
        
        if response.status_code != 200:
            print(f"[Erro API] Status {response.status_code}: {response.text}")
            return []
            
        data = response.json()
        results = data.get("data") or data.get("results") or []
        if not results and isinstance(data, list):
            results = data

        print(f"[Scraping] API retornou {len(results)} resultados brutos. Filtrando até {max_resultados}...")
        
        leads = []
        # FATIANDO A LISTA AQUI para respeitar o max
        for place in results[:max_resultados]:
            nome = place.get("Name") or place.get("name") or place.get("title") or ""
            site = place.get("Website") or place.get("website") or ""
            telefone = place.get("Phone") or place.get("phone") or ""
            endereco = place.get("Full Address") or place.get("address") or place.get("full_address") or ""
            
            if not nome:
                continue
                
            # Tenta pegar WhatsApp e E-mail no site
            whatsapp, email = extrair_contatos_do_site(site)

            # Fallback: se não achou WhatsApp no site, usa o telefone do Maps
            if not whatsapp and telefone:
                digits = re.sub(r"\D", "", telefone)
                if len(digits) >= 10 and len(digits) <= 11:
                    whatsapp = telefone

            mensagem = mensagem_template.format(nome=nome, cidade=cidade)
            link_wa = gerar_link_wa(whatsapp, mensagem) if whatsapp else ""

            lead = {
                "nome": nome,
                "email": email,
                "whatsapp": whatsapp,
                "telefone": telefone,
                "endereco": endereco,
                "site": site,
                "link_whatsapp": link_wa,
            }
            leads.append(lead)
            
        return leads

    except requests.exceptions.RequestException as e:
        print(f"[Erro de Conexão] {e}")
        return []

# =========================================================
# FLUXO PRINCIPAL
# =========================================================
def processar(nicho: dict, cidade: str, max_resultados: int):
    conn = init_db()
    leads = scrape_leads(nicho["search"], cidade, nicho["mensagem"], max_resultados)
    
    if not leads:
        print("Nenhum lead coletado. Verifique os erros acima.")
        conn.close()
        return

    salvos = 0
    duplicados = 0
    com_whatsapp = 0
    com_email = 0
    
    for lead in leads:
        # A função salvar_lead agora retorna True (salvou) ou False (duplicado)
        if salvar_lead(conn, lead, cidade, nicho["key"]):
            salvos += 1
            if lead.get("whatsapp"): com_whatsapp += 1
            if lead.get("email"): com_email += 1
        else:
            duplicados += 1

    print(f"\n[Resumo] {salvos} leads novos salvos | {duplicados} duplicados ignorados")
    print(f"          {com_whatsapp} com WhatsApp | {com_email} com E-mail")
    conn.close()

def exportar_csv():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT nome, whatsapp, email, telefone, endereco, cidade, nicho, link_whatsapp FROM leads ORDER BY id",
        conn,
    )
    conn.close()
    
    caminho = BASE_DIR / "leads_whatsapp.csv"
    df.to_csv(caminho, index=False, encoding="utf-8-sig")
    print(f"\n[Export] {len(df)} leads totais exportados para {caminho.name}")
    print("→ Abra o CSV, clique no link da coluna 'link_whatsapp' e o WhatsApp Web abrirá pronto para envio.")

def menu_interativo():
    print("\n=== Selecione o nicho de prospecção ===")
    for k, v in NICHOS.items():
        print(f"  [{k:>2}] {v['label']}")
    while True:
        escolha = input("\nNúmero do nicho: ").strip()
        if escolha in NICHOS:
            return NICHOS[escolha]
        print("  Opção inválida.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extração de leads + links WhatsApp")
    parser.add_argument("--nicho", help="Chave do nicho (ex: pizzarias)")
    parser.add_argument("--cidade", help="Cidade (ex: 'Americana - SP')")
    parser.add_argument("--max", type=int, default=50, help="Máx. de resultados")
    args = parser.parse_args()

    if args.nicho:
        matches = [v for v in NICHOS.values() if v["key"] == args.nicho]
        if not matches:
            print(f"Nicho inválido.")
            raise SystemExit(1)
        nicho = matches[0]
    else:
        nicho = menu_interativo()

    cidade = args.cidade or input("Cidade: ").strip()
    if not cidade:
        raise SystemExit(1)

    print(f"\n▶ Nicho: {nicho['label']} | Cidade: {cidade} | Máx: {args.max}\n")
    processar(nicho, cidade, args.max)
    exportar_csv()