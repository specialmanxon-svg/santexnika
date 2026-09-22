"""Reconciliation Act Generator service (HTML & PDF) for Diyorgroup platform.

Generates official reconciliation acts (Акт сверки взаиморасчетов / Ўзаро ҳисоб-китоб далолатномаси)
with full Unicode/UTF-8 Cyrillic & Uzbek font support (ў, қ, ғ, ҳ) using ReportLab.
"""
import io
import os
from datetime import datetime
from typing import Optional
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.debt import DebtRegistry
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = structlog.get_logger(__name__)

# ==============================================================================
# UNICODE / UTF-8 FONT REGISTRATION
# ==============================================================================
FONT_NAME = "Arial"
FONT_NAME_BOLD = "Arial-Bold"
FONT_NAME_ITALIC = "Arial-Italic"
FONT_NAME_BOLD_ITALIC = "Arial-BoldItalic"


def register_cyrillic_fonts() -> str:
    """Registers Unicode TrueType fonts supporting Cyrillic and Uzbek characters.
    
    Registers regular, bold, italic, and bold-italic variants, then registers
    the font family so ReportLab's Paragraph parser correctly renders <b> tags
    with Arial-Bold instead of falling back to Helvetica-Bold (which produces ■■■).
    """
    registered = pdfmetrics.getRegisteredFontNames()
    if "Arial" in registered and "Arial-Bold" in registered:
        return "Arial"

    # Search candidates across Windows and Linux environments
    candidates = [
        # Windows Fonts standard directory
        (
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\ariali.ttf",
            r"C:\Windows\Fonts\arialbi.ttf",
        ),
        (
            r"C:\Windows\Fonts\Arial.ttf",
            r"C:\Windows\Fonts\Arialbd.ttf",
            r"C:\Windows\Fonts\Ariali.ttf",
            r"C:\Windows\Fonts\Arialbi.ttf",
        ),
        # Linux standard fonts (Debian / Ubuntu / Docker / Alpine)
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
        ),
    ]

    for norm, bold, it, bi in candidates:
        if os.path.exists(norm) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("Arial", norm))
                pdfmetrics.registerFont(TTFont("Arial-Bold", bold))
                if it and os.path.exists(it):
                    pdfmetrics.registerFont(TTFont("Arial-Italic", it))
                else:
                    pdfmetrics.registerFont(TTFont("Arial-Italic", norm))

                if bi and os.path.exists(bi):
                    pdfmetrics.registerFont(TTFont("Arial-BoldItalic", bi))
                else:
                    pdfmetrics.registerFont(TTFont("Arial-BoldItalic", bold))

                pdfmetrics.registerFontFamily(
                    "Arial",
                    normal="Arial",
                    bold="Arial-Bold",
                    italic="Arial-Italic",
                    boldItalic="Arial-BoldItalic",
                )
                logger.info("cyrillic_fonts_registered_successfully", normal=norm, bold=bold)
                return "Arial"
            except Exception as e:
                logger.warning("font_registration_failed", path=norm, error=str(e))

    logger.error("no_cyrillic_font_found_fallback_to_helvetica")
    return "Helvetica"


# Initialize font on module load
FONT_NAME = register_cyrillic_fonts()
FONT_NAME_BOLD = "Arial-Bold" if FONT_NAME == "Arial" else "Helvetica-Bold"


def format_date(d_val: Optional[str]) -> str:
    """Helper to format YYYY-MM-DD to DD.MM.YYYY."""
    if not d_val:
        return "—"
    s = str(d_val)[:10]
    parts = s.split("-")
    if len(parts) == 3:
        return f"{parts[2]}.{parts[1]}.{parts[0]}"
    return s


# ==============================================================================
# DATA FETCHER (MoySklad API + Database Fallback)
# ==============================================================================
async def get_debtor_info(company_target: str, session: AsyncSession = None) -> dict:
    target = (company_target or "").strip()
    # 0. Quick return for demo
    if target.lower() in ("demo", "cp-demo"):
        return {
            "id": "demo",
            "moysklad_id": "cp-demo",
            "title": "«ФАРРУХ АКА РЕГЕН КЛИНИКА» МЧЖ",
            "phone": "+998 91 132 71 41",
            "inn": "304859123",
            "debt_sum": 15542000.0,
            "balance": -15542000.0,
            "is_blocked": True,
            "date": datetime.now().strftime("%d.%m.%Y"),
            "operations": [],
            "total_debit_usd": 0.0,
            "total_credit_usd": 0.0,
            "saldo_usd": 0.0,
            "total_debit_uzs": 15542000.0,
            "total_credit_uzs": 0.0,
            "saldo_uzs": 15542000.0,
            "total_debit": 15542000.0,
            "total_credit": 0.0,
        }

    # 1. Fetch from MoySklad API
    try:
        from services.moysklad_client import MoySkladClient

        ms_client = MoySkladClient()
        if await ms_client.is_configured():
            target_id = target
            if not target_id or target_id == "default":
                debtors = await ms_client.get_counterparties_by_segment("debtors", limit=1)
                if debtors:
                    target_id = debtors[0]["id"]

            if target_id and target_id not in ("default", "demo"):
                rec = await ms_client.get_counterparty_reconciliation(target_id)
                await ms_client.close()
                if rec and rec.get("name"):
                    debt_val = float(rec.get("debt_sum", 0.0))
                    return {
                        "id": target_id,
                        "moysklad_id": target_id,
                        "title": rec.get("name"),
                        "phone": rec.get("phone") or "—",
                        "inn": rec.get("inn") or "—",
                        "company_type": rec.get("company_type", "legal"),
                        "debt_sum": debt_val,
                        "balance": rec.get("balance", 0.0),
                        "is_blocked": True if (debt_val > 0 or rec.get("saldo_usd", 0) > 0 or rec.get("saldo_uzs", 0) > 0) else False,
                        "date": datetime.now().strftime("%d.%m.%Y"),
                        "operations": rec.get("operations", []),
                        "total_debit_usd": rec.get("total_debit_usd", 0.0),
                        "total_credit_usd": rec.get("total_credit_usd", 0.0),
                        "saldo_usd": rec.get("saldo_usd", 0.0),
                        "total_debit_uzs": rec.get("total_debit_uzs", 0.0),
                        "total_credit_uzs": rec.get("total_credit_uzs", 0.0),
                        "saldo_uzs": rec.get("saldo_uzs", 0.0),
                        "total_debit": rec.get("total_debit", 0.0),
                        "total_credit": rec.get("total_credit", 0.0),
                    }
        await ms_client.close()
    except Exception as e:
        logger.warning("moysklad_reconciliation_fetch_failed", error=str(e), target=company_target)

    # 2. Fallback to DebtRegistry in database if session available
    if session:
        try:
            stmt = select(DebtRegistry)
            res = await session.execute(stmt)
            debts = res.scalars().all()
            target_l = target.lower()
            selected = None
            if target_l and target_l != "default":
                for d in debts:
                    if target_l in d.company_title.lower() or target_l in d.company_moysklad_id.lower() or str(d.id) == target_l:
                        selected = d
                        break
            if not selected:
                debts_sorted = sorted(debts, key=lambda x: float(x.debt_60_plus or 0.0), reverse=True)
                selected = debts_sorted[0] if debts_sorted else None
            if selected:
                debt_val = float(selected.debt_60_plus or selected.debt_0_30 or 0.0)
                return {
                    "id": str(selected.id),
                    "moysklad_id": selected.company_moysklad_id,
                    "title": selected.company_title,
                    "phone": "—",
                    "inn": "—",
                    "debt_sum": debt_val,
                    "balance": -debt_val,
                    "is_blocked": selected.is_blocked,
                    "date": datetime.now().strftime("%d.%m.%Y"),
                    "operations": [],
                    "total_debit_usd": 0.0,
                    "total_credit_usd": 0.0,
                    "saldo_usd": 0.0,
                    "total_debit_uzs": debt_val,
                    "total_credit_uzs": 0.0,
                    "saldo_uzs": debt_val,
                    "total_debit": debt_val,
                    "total_credit": 0.0,
                }
        except Exception as e:
            logger.warning("debt_registry_fetch_failed", error=str(e))

    # 3. Default demo fallback
    return {
        "id": "demo",
        "moysklad_id": "cp-demo",
        "title": "«ФАРРУХ АКА РЕГЕН КЛИНИКА» МЧЖ",
        "phone": "+998 91 132 71 41",
        "inn": "304859123",
        "debt_sum": 15542000.0,
        "balance": -15542000.0,
        "is_blocked": True,
        "date": datetime.now().strftime("%d.%m.%Y"),
        "operations": [],
        "total_debit_usd": 0.0,
        "total_credit_usd": 0.0,
        "saldo_usd": 0.0,
        "total_debit_uzs": 15542000.0,
        "total_credit_uzs": 0.0,
        "saldo_uzs": 15542000.0,
        "total_debit": 15542000.0,
        "total_credit": 0.0,
    }


# ==============================================================================
# HTML ACT GENERATION (Printable & Browser View)
# ==============================================================================
async def generate_act_html(company_target: str, session: AsyncSession = None) -> str:
    """Generates official Reconciliation Act in print-ready, modern HTML with real MoySklad transactions."""
    info = await get_debtor_info(company_target, session)

    total_debit_usd = info.get("total_debit_usd", 0.0)
    total_credit_usd = info.get("total_credit_usd", 0.0)
    saldo_usd = info.get("saldo_usd", 0.0)

    total_debit_uzs = info.get("total_debit_uzs", 0.0)
    total_credit_uzs = info.get("total_credit_uzs", 0.0)
    saldo_uzs = info.get("saldo_uzs", 0.0)

    # Status descriptions and badges
    if saldo_usd > 0.01:
        usd_desc = "Ҳамкорнинг «Diyor Group» олдидаги қарздорлиги"
        usd_color = "#dc2626"
        usd_badge = "Қарздорлик"
    elif saldo_usd < -0.01:
        usd_desc = "Ҳамкорнинг бўнак (аванс) тўлови"
        usd_color = "#059669"
        usd_badge = "Аванс"
    else:
        usd_desc = "Долларда ўзаро қарздорлик мавжуд эмас"
        usd_color = "#475569"
        usd_badge = "0.00"

    if saldo_uzs > 0.01:
        uzs_desc = "Ҳамкорнинг «Diyor Group» олдидаги қарздорлиги"
        uzs_color = "#dc2626"
        uzs_badge = "Қарздорлик"
    elif saldo_uzs < -0.01:
        uzs_desc = "Ҳамкорнинг бўнак (аванс) тўлови"
        uzs_color = "#059669"
        uzs_badge = "Аванс"
    else:
        uzs_desc = "Сўмда ўзаро қарздорлик мавжуд эмас"
        uzs_color = "#475569"
        uzs_badge = "0.00"

    # Generate operation rows
    rows_html = ""
    ops = info.get("operations", [])
    
    running_uzs = 0.0
    running_usd = 0.0

    # Initial balance row
    rows_html += f"""
        <tr style="background: #f8fafc; font-style: italic;">
            <td class="center">—</td>
            <td class="center font-mono">{ops[0].get('date', '01.01.2026') if ops else '01.01.2026'}</td>
            <td><b>Башлангыч қолдиқ (Сальдо начальное)</b></td>
            <td class="num">—</td>
            <td class="num">—</td>
            <td class="num">0.00 сўм</td>
        </tr>"""

    if ops:
        for idx, op in enumerate(ops, 1):
            curr = op.get("currency", "UZS")
            is_usd = (curr == "USD")
            badge = (
                '<span class="curr-badge badge-usd">$ USD</span>'
                if is_usd
                else '<span class="curr-badge badge-uzs">UZS сўм</span>'
            )

            d_val = op.get("debit_usd" if is_usd else "debit_uzs", 0.0)
            c_val = op.get("credit_usd" if is_usd else "credit_uzs", 0.0)

            if is_usd:
                running_usd += d_val - c_val
                r_val = running_usd
                cur_sym = "$"
                d_str = f"$ {d_val:,.2f}".replace(",", " ") if d_val > 0 else "—"
                c_str = f"$ {c_val:,.2f}".replace(",", " ") if c_val > 0 else "—"
                r_tag = " (Қарз)" if r_val > 0.01 else (" (Аванс)" if r_val < -0.01 else "")
                r_str = f"$ {abs(r_val):,.2f}{r_tag}".replace(",", " ")
            else:
                running_uzs += d_val - c_val
                r_val = running_uzs
                cur_sym = "сўм"
                d_str = f"{d_val:,.0f} сўм".replace(",", " ") if d_val > 0 else "—"
                c_str = f"{c_val:,.0f} сўм".replace(",", " ") if c_val > 0 else "—"
                r_tag = " (Қарз)" if r_val > 0.01 else (" (Аванс)" if r_val < -0.01 else "")
                r_str = f"{abs(r_val):,.0f} сўм{r_tag}".replace(",", " ")

            date_disp = format_date(op.get("date") or op.get("moment"))
            desc_text = op.get("description", "")

            rows_html += f"""
                <tr>
                    <td class="center">{idx}</td>
                    <td class="center font-mono">{date_disp}</td>
                    <td><b>{desc_text}</b> {badge}</td>
                    <td class="num {'usd-val' if is_usd else 'uzs-val'}">{d_str}</td>
                    <td class="num {'usd-val' if is_usd else 'uzs-val'}">{c_str}</td>
                    <td class="num" style="font-weight: 700; color: {'#dc2626' if r_val > 0.01 else '#059669'};">{r_str}</td>
                </tr>"""

        tot_d_uzs_str = f"{total_debit_uzs:,.0f} сўм".replace(",", " ")
        tot_c_uzs_str = f"{total_credit_uzs:,.0f} сўм".replace(",", " ")
        tot_d_usd_str = f"$ {total_debit_usd:,.2f}".replace(",", " ")
        tot_c_usd_str = f"$ {total_credit_usd:,.2f}".replace(",", " ")
    else:
        debt_f = f"{info['debt_sum']:,.0f} сўм".replace(",", " ")
        rows_html += f"""
                <tr>
                    <td class="center">1</td>
                    <td class="center font-mono">{info['date']}</td>
                    <td><b>Ҳисобланган товарлар (МойСклад)</b> <span class="curr-badge badge-uzs">UZS сўм</span></td>
                    <td class="num uzs-val">{debt_f}</td>
                    <td class="num uzs-val">—</td>
                    <td class="num" style="font-weight: 700; color: #dc2626;">{debt_f} (Қарз)</td>
                </tr>"""
        tot_d_uzs_str = debt_f
        tot_c_uzs_str = "0 сўм"
        tot_d_usd_str = "$ 0.00"
        tot_c_usd_str = "$ 0.00"

    saldo_uzs_str = f"{abs(saldo_uzs):,.0f} сўм".replace(",", " ")
    saldo_usd_str = f"$ {abs(saldo_usd):,.2f}".replace(",", " ")

    has_usd = (total_debit_usd > 0 or total_credit_usd > 0 or abs(saldo_usd) > 0.01)

    html = f"""<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <title>Ўзаро ҳисоб-китоб далолатномаси (Акт сверки) — {info['title']}</title>
    <style>
        @page {{ size: A4 portrait; margin: 12mm; }}
        body {{
            font-family: Arial, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: #0f172a;
            background: #f1f5f9;
            margin: 0;
            padding: 24px;
        }}
        .act-container {{
            max-width: 960px;
            margin: 0 auto;
            background: #ffffff;
            padding: 36px 44px;
            box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.05);
            border-radius: 12px;
            border: 1px solid #e2e8f0;
        }}
        .header {{
            text-align: center;
            border-bottom: 2px solid #1e3a8a;
            padding-bottom: 14px;
            margin-bottom: 18px;
        }}
        .header h1 {{
            margin: 0 0 6px 0;
            font-size: 20px;
            color: #1e3a8a;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 800;
        }}
        .header p {{
            margin: 0;
            font-size: 12.5px;
            color: #64748b;
            font-weight: 500;
        }}
        .parties {{
            font-size: 12.5px;
            line-height: 1.6;
            margin-bottom: 20px;
            background: #f8fafc;
            padding: 16px 18px;
            border-left: 4px solid #1e3a8a;
            border-radius: 6px;
            border: 1px solid #e2e8f0;
            border-left-width: 4px;
        }}
        .requisites {{
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            color: #334155;
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px dashed #cbd5e1;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
            margin-bottom: 20px;
        }}
        th, td {{
            border: 1px solid #cbd5e1;
            padding: 7px 9px;
            text-align: left;
        }}
        th {{
            font-weight: 700;
            text-align: center;
            background: #1e3a8a;
            color: #ffffff;
            font-size: 11px;
        }}
        .num {{
            text-align: right;
            font-family: Arial, monospace;
            white-space: nowrap;
        }}
        .center {{ text-align: center; }}
        .font-mono {{ font-family: Arial, monospace; font-size: 11px; }}
        .dash {{ color: #cbd5e1; }}
        .usd-val {{ color: #1e3a8a; font-weight: 600; }}
        .uzs-val {{ color: #065f46; font-weight: 600; }}
        .total-row {{
            background: #f8fafc;
            font-weight: 700;
        }}
        .curr-badge {{
            display: inline-block;
            font-size: 9.5px;
            font-weight: 700;
            padding: 1px 5px;
            border-radius: 4px;
            margin-left: 6px;
            vertical-align: middle;
        }}
        .badge-usd {{ background: #dbeafe; color: #1e40af; border: 1px solid #bfdbfe; }}
        .badge-uzs {{ background: #dcfce7; color: #166534; border: 1px solid #bbf7d0; }}
        .conclusion {{
            font-size: 12.5px;
            margin-bottom: 24px;
            padding: 16px;
            background: #ffffff;
            border: 2px solid #e2e8f0;
            border-radius: 8px;
        }}
        .signatures {{
            display: flex;
            justify-content: space-between;
            margin-top: 25px;
            padding-top: 20px;
            border-top: 1px solid #e2e8f0;
        }}
        .sign-col {{
            width: 46%;
            font-size: 12px;
            line-height: 1.8;
        }}
        .sign-line {{
            margin-top: 35px;
            border-bottom: 1px solid #334155;
            width: 85%;
        }}
        .stamp-box {{
            color: #94a3b8;
            font-size: 11px;
            margin-top: 6px;
        }}
        .actions-bar {{
            max-width: 960px;
            margin: 0 auto 16px auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .btn {{
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            border: none;
            transition: all 0.2s;
        }}
        .btn-print {{ background: #1e3a8a; color: white; }}
        .btn-download {{ background: #059669; color: white; }}
        @media print {{
            body {{ background: white; padding: 0; }}
            .actions-bar {{ display: none; }}
            .act-container {{ box-shadow: none; padding: 0; border: none; max-width: 100%; }}
        }}
    </style>
</head>
<body>
    <div class="actions-bar">
        <div style="font-size: 12.5px; font-weight: 700; color: #1e3a8a;">
            🏢 «Diyor Group» МЧЖ • Расмий Акт сверки
        </div>
        <div style="display: flex; gap: 10px;">
            <button class="btn btn-print" onclick="window.print()">🖨️ Чоп этиш (Печать)</button>
            <a class="btn btn-download" href="/api/v1/debts/act-download/{info['moysklad_id']}">⬇️ PDF юклаб олиш</a>
        </div>
    </div>

    <div class="act-container">
        <div class="header">
            <h1>Ўзаро ҳисоб-китоблар солиштирма далолатномаси</h1>
            <p>(Акт сверки взаимных расчетов) • Ҳолати: {info['date']} йил (МойСклад Live)</p>
        </div>

        <div class="parties">
            Биз, қуйида имзо чекувчилар, бир тарафдан <b>«Diyor Group» МЧЖ</b> (Етказиб берувчи / ООО «Diyor Group»), ва иккинчи тарафдан <b>{info['title']}</b> (Харидор / Ҳамкор), ушбу далолатномани туздик, шу ҳақдаки, бухгалтерия ҳисоб-китоб маълумотларига кўра ўзаро ҳисоб-китоблар ҳолати қуйидагича:
            <div class="requisites">
                <div><b>Етказиб берувчи:</b> «Diyor Group» МЧЖ | ИНН: 311858563 | Тел: +998 93 383 03 70 / +998 88 870 00 70</div>
                <div><b>Ҳамкор:</b> {info['title']} | ИНН: {info.get('inn') or '—'} | Тел: {info.get('phone') or '—'}</div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th style="width: 32px;">№</th>
                    <th style="width: 85px;">Сана</th>
                    <th>Ҳужжат номи ва тури (Операция)</th>
                    <th style="width: 120px;">Дебет</th>
                    <th style="width: 120px;">Кредит</th>
                    <th style="width: 140px;">Қалдиқ (Сальдо)</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
                <tr class="total-row" style="border-top: 2px solid #1e3a8a; background: #f8fafc;">
                    <td colspan="3" style="text-align: right; font-weight: 800;">Жами айланмалар (UZS сўм):</td>
                    <td class="num uzs-val" style="font-weight: 800;">{tot_d_uzs_str}</td>
                    <td class="num uzs-val" style="font-weight: 800;">{tot_c_uzs_str}</td>
                    <td class="num" style="font-weight: 800; color: {'#dc2626' if saldo_uzs > 0.01 else '#059669'};">{saldo_uzs_str} ({uzs_badge})</td>
                </tr>
                {f'''
                <tr class="total-row" style="background: #eff6ff;">
                    <td colspan="3" style="text-align: right; font-weight: 800;">Жами айланмалар ($ USD):</td>
                    <td class="num usd-val" style="font-weight: 800;">{tot_d_usd_str}</td>
                    <td class="num usd-val" style="font-weight: 800;">{tot_c_usd_str}</td>
                    <td class="num" style="font-weight: 800; color: {'#dc2626' if saldo_usd > 0.01 else '#059669'};">{saldo_usd_str} ({usd_badge})</td>
                </tr>''' if has_usd else ''}
            </tbody>
        </table>

        <div class="conclusion">
            <div style="font-size: 13px; font-weight: 800; color: #1e3a8a; margin-bottom: 10px; text-transform: uppercase;">
                📌 Ўзаро ҳисоб-китоблар якуни ({info['date']} йил ҳолатига):
            </div>
            <div style="line-height: 1.8; color: #1e293b;">
                • <b>Сўмда (UZS):</b> <b>{saldo_uzs_str}</b> — <i>{uzs_desc}</i><br>
                {f'• <b>Долларда (USD):</b> <b>{saldo_usd_str}</b> — <i>{usd_desc}</i><br>' if has_usd else ''}
            </div>
        </div>

        <div class="signatures">
            <div class="sign-col">
                <b>«Diyor Group» МЧЖ номидан:</b><br>
                Бош директор / Бош ҳисобчи<br>
                <div class="sign-line"></div>
                <div class="stamp-box">М.П. / Имзо</div>
            </div>
            <div class="sign-col">
                <b>{info['title']} номидан:</b><br>
                Раҳбар / Масъул шахс<br>
                <div class="sign-line"></div>
                <div class="stamp-box">М.П. / Имзо</div>
            </div>
        </div>
    </div>
</body>
</html>"""
    return html


# ==============================================================================
# OFFICIAL PDF ACT GENERATION (ReportLab with Full UTF-8 Cyrillic Support)
# ==============================================================================
async def generate_act_pdf(company_target: str, session: AsyncSession = None) -> io.BytesIO:
    """Generates official Reconciliation Act PDF document with 100% Cyrillic/Uzbek UTF-8 support.
    
    Structure:
    1. Header: «Diyor Group» МЧЖ & Counterparty requisites, date, initial balance.
    2. Table: № | Сана | Ҳужжат номи ва тури | Дебет | Кредит | Қалдиқ (Сальдо).
    3. Footer: Turnover totals, final saldo, and official signature placeholders.
    """
    # Ensure font registration is active
    font_name = register_cyrillic_fonts()
    bold_font = "Arial-Bold" if font_name == "Arial" else "Helvetica-Bold"

    info = await get_debtor_info(company_target, session)

    total_debit_usd = info.get("total_debit_usd", 0.0)
    total_credit_usd = info.get("total_credit_usd", 0.0)
    saldo_usd = info.get("saldo_usd", 0.0)

    total_debit_uzs = info.get("total_debit_uzs", 0.0)
    total_credit_uzs = info.get("total_credit_uzs", 0.0)
    saldo_uzs = info.get("saldo_uzs", 0.0)

    buf = io.BytesIO()
    # A4 Portrait: 595.27 x 841.89 pt. Margin: 28 pt -> Usable width: 539 pt.
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=28,
        rightMargin=28,
        topMargin=26,
        bottomMargin=26,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ActTitle",
        fontName=bold_font,
        fontSize=12.5,
        leading=15,
        alignment=1,  # Center
        textColor=colors.HexColor("#1E3A8A"),
        spaceAfter=3,
    )

    sub_style = ParagraphStyle(
        "ActSub",
        fontName=font_name,
        fontSize=8.5,
        leading=11,
        alignment=1,  # Center
        textColor=colors.HexColor("#475569"),
        spaceAfter=8,
    )

    parties_style = ParagraphStyle(
        "ActParties",
        fontName=font_name,
        fontSize=7.5,
        leading=10.5,
        textColor=colors.HexColor("#1E293B"),
    )

    table_header_style = ParagraphStyle(
        "THCell",
        fontName=bold_font,
        fontSize=7.5,
        leading=9.5,
        alignment=1,  # Center
        textColor=colors.white,
    )

    cell_left_style = ParagraphStyle(
        "CellLeft",
        fontName=font_name,
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#0F172A"),
    )

    cell_center_style = ParagraphStyle(
        "CellCenter",
        fontName=font_name,
        fontSize=7,
        leading=9,
        alignment=1,  # Center
        textColor=colors.HexColor("#0F172A"),
    )

    cell_num_style = ParagraphStyle(
        "CellNum",
        fontName=font_name,
        fontSize=7,
        leading=9,
        alignment=2,  # Right
        textColor=colors.HexColor("#0F172A"),
    )

    cell_bold_num_style = ParagraphStyle(
        "CellBoldNum",
        fontName=bold_font,
        fontSize=7,
        leading=9,
        alignment=2,  # Right
        textColor=colors.HexColor("#1E3A8A"),
    )

    conclusion_style = ParagraphStyle(
        "ActConclusion",
        fontName=font_name,
        fontSize=8,
        leading=11.5,
        textColor=colors.HexColor("#0F172A"),
    )

    elements = []

    # 1. Header
    elements.append(Paragraph("ЎЗАРО ҲИСОБ-КИТОБЛАР СОЛИШТИРМА ДАЛОЛАТНОМАСИ", title_style))
    elements.append(Paragraph(f"(АКТ СВЕРКИ ВЗАИМНЫХ РАСЧЕТОВ) • Ҳолати: {info['date']} йил (МойСклад Live)", sub_style))

    # 2. Parties Requisites Box
    parties_text = (
        f"Биз, қуйида имзо чекувчилар, бир тарафдан <b>«Diyor Group» МЧЖ</b> (Етказиб берувчи / ООО «Diyor Group», "
        f"ИНН: 311858563, Тел: +998 93 383 03 70 / +998 88 870 00 70), ва иккинчи тарафдан "
        f"<b>{info['title']}</b> (Ҳамкор / Контрагент, ИНН: {info.get('inn') or '—'}, Тел: {info.get('phone') or '—'}), "
        f"ушбу далолатномани туздик, шу ҳақдаки, бухгалтерия ҳисоб-китоб маълумотларига кўра ўзаро ҳисоб-китоблар ҳолати қуйидагича:"
    )
    elements.append(Paragraph(parties_text, parties_style))
    elements.append(Spacer(1, 6))

    # 3. Table: № | Сана | Ҳужжат номи / тури | Дебет | Кредит | Қалдиқ (Сальдо)
    # Total width: 22 + 56 + 219 + 80 + 80 + 80 = 537 pt
    col_widths = [22, 56, 219, 80, 80, 80]

    table_data = [
        [
            Paragraph("№", table_header_style),
            Paragraph("Сана", table_header_style),
            Paragraph("Ҳужжат номи ва тури (Операция)", table_header_style),
            Paragraph("Дебет", table_header_style),
            Paragraph("Кредит", table_header_style),
            Paragraph("Қалдиқ (Сальдо)", table_header_style),
        ]
    ]

    ops = info.get("operations", [])
    running_uzs = 0.0
    running_usd = 0.0

    # Initial balance row
    start_date = format_date(ops[0].get("date") or ops[0].get("moment")) if ops else "01.01.2026"
    table_data.append([
        Paragraph("—", cell_center_style),
        Paragraph(start_date, cell_center_style),
        Paragraph("<b>Башлангыч қолдиқ (Сальдо начальное)</b>", cell_left_style),
        Paragraph("—", cell_center_style),
        Paragraph("—", cell_center_style),
        Paragraph("0 сўм", cell_num_style),
    ])

    if ops:
        for idx, op in enumerate(ops[:50], 1):  # Display up to 50 operations
            curr = op.get("currency", "UZS")
            is_usd = (curr == "USD")

            d_val = op.get("debit_usd" if is_usd else "debit_uzs", 0.0)
            c_val = op.get("credit_usd" if is_usd else "credit_uzs", 0.0)

            if is_usd:
                running_usd += d_val - c_val
                r_val = running_usd
                d_str = f"$ {d_val:,.2f}".replace(",", " ") if d_val > 0 else "—"
                c_str = f"$ {c_val:,.2f}".replace(",", " ") if c_val > 0 else "—"
                r_tag = " (Қарз)" if r_val > 0.01 else (" (Аванс)" if r_val < -0.01 else "")
                r_str = f"$ {abs(r_val):,.2f}{r_tag}".replace(",", " ")
            else:
                running_uzs += d_val - c_val
                r_val = running_uzs
                d_str = f"{d_val:,.0f} сўм".replace(",", " ") if d_val > 0 else "—"
                c_str = f"{c_val:,.0f} сўм".replace(",", " ") if c_val > 0 else "—"
                r_tag = " (Қарз)" if r_val > 0.01 else (" (Аванс)" if r_val < -0.01 else "")
                r_str = f"{abs(r_val):,.0f} сўм{r_tag}".replace(",", " ")

            date_disp = format_date(op.get("date") or op.get("moment"))
            desc_text = f"{op.get('description', '')} <b>[{curr}]</b>"

            table_data.append([
                Paragraph(str(idx), cell_center_style),
                Paragraph(date_disp, cell_center_style),
                Paragraph(desc_text, cell_left_style),
                Paragraph(d_str, cell_num_style),
                Paragraph(c_str, cell_num_style),
                Paragraph(r_str, cell_bold_num_style if r_val > 0.01 else cell_num_style),
            ])

        tot_d_uzs_s = f"{total_debit_uzs:,.0f} сўм".replace(",", " ")
        tot_c_uzs_s = f"{total_credit_uzs:,.0f} сўм".replace(",", " ")
        tot_d_usd_s = f"$ {total_debit_usd:,.2f}".replace(",", " ")
        tot_c_usd_s = f"$ {total_credit_usd:,.2f}".replace(",", " ")
    else:
        debt_f = f"{info['debt_sum']:,.0f} сўм".replace(",", " ")
        table_data.append([
            Paragraph("1", cell_center_style),
            Paragraph(info["date"], cell_center_style),
            Paragraph("Ҳисобланган қарздорлик (МойСклад) <b>[UZS]</b>", cell_left_style),
            Paragraph(debt_f, cell_num_style),
            Paragraph("—", cell_center_style),
            Paragraph(f"{debt_f} (Қарз)", cell_bold_num_style),
        ])
        tot_d_uzs_s = debt_f
        tot_c_uzs_s = "0 сўм"
        tot_d_usd_s = "$ 0.00"
        tot_c_usd_s = "$ 0.00"

    # Summary rows
    uzs_saldo_badge = "Қарз" if saldo_uzs > 0.01 else ("Аванс" if saldo_uzs < -0.01 else "0.00")
    uzs_saldo_final = f"{abs(saldo_uzs):,.0f} сўм ({uzs_saldo_badge})".replace(",", " ")

    summary_rows_count = 1
    table_data.append([
        Paragraph("", cell_center_style),
        Paragraph("<b>Жами (UZS):</b>", cell_left_style),
        Paragraph("", cell_left_style),
        Paragraph(f"<b>{tot_d_uzs_s}</b>", cell_bold_num_style),
        Paragraph(f"<b>{tot_c_uzs_s}</b>", cell_bold_num_style),
        Paragraph(f"<b>{uzs_saldo_final}</b>", cell_bold_num_style),
    ])

    has_usd = (total_debit_usd > 0 or total_credit_usd > 0 or abs(saldo_usd) > 0.01)
    if has_usd:
        summary_rows_count += 1
        usd_saldo_badge = "Қарз" if saldo_usd > 0.01 else ("Аванс" if saldo_usd < -0.01 else "0.00")
        usd_saldo_final = f"$ {abs(saldo_usd):,.2f} ({usd_saldo_badge})".replace(",", " ")
        table_data.append([
            Paragraph("", cell_center_style),
            Paragraph("<b>Жами (USD):</b>", cell_left_style),
            Paragraph("", cell_left_style),
            Paragraph(f"<b>{tot_d_usd_s}</b>", cell_bold_num_style),
            Paragraph(f"<b>{tot_c_usd_s}</b>", cell_bold_num_style),
            Paragraph(f"<b>{usd_saldo_final}</b>", cell_bold_num_style),
        ])

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    num_rows = len(table_data)

    t_style = [
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]

    # Style summary rows
    start_summary_idx = num_rows - summary_rows_count
    for r_idx in range(start_summary_idx, num_rows):
        t_style.extend([
            ("BACKGROUND", (0, r_idx), (-1, r_idx), colors.HexColor("#F1F5F9")),
            ("SPAN", (1, r_idx), (2, r_idx)),
        ])

    t.setStyle(TableStyle(t_style))
    elements.append(t)
    elements.append(Spacer(1, 8))

    # 4. Conclusion
    if saldo_uzs > 0.01:
        uzs_conclusion = f"Ҳамкорнинг «Diyor Group» МЧЖ олдидаги қарздорлиги <b>{abs(saldo_uzs):,.0f} сўм</b>ни ташкил этади".replace(",", " ")
    elif saldo_uzs < -0.01:
        uzs_conclusion = f"«Diyor Group» МЧЖнинг Ҳамкор олдидаги қарздорлиги (бўнак/аванс) <b>{abs(saldo_uzs):,.0f} сўм</b>ни ташкил этади".replace(",", " ")
    else:
        uzs_conclusion = "Сўмдаги ўзаро қарздорлик мавжуд эмас (0 сўм)"

    if has_usd:
        if saldo_usd > 0.01:
            usd_conclusion = f"Ҳамкорнинг «Diyor Group» МЧЖ олдидаги қарздорлиги <b>$ {abs(saldo_usd):,.2f}</b>ни ташкил этади".replace(",", " ")
        elif saldo_usd < -0.01:
            usd_conclusion = f"«Diyor Group» МЧЖнинг Ҳамкор олдидаги қарздорлиги (бўнак/аванс) <b>$ {abs(saldo_usd):,.2f}</b>ни ташкил этади".replace(",", " ")
        else:
            usd_conclusion = "Доллардаги ўзаро қарздорлик мавжуд эмас ($ 0.00)"
        
        conclusion_text = (
            f"<b>Ҳисоб-китоблар якуни ({info['date']} йил ҳолатига):</b><br/>"
            f"1. <b>Сўмда (UZS):</b> {uzs_conclusion}.<br/>"
            f"2. <b>Долларда (USD):</b> {usd_conclusion}."
        )
    else:
        conclusion_text = (
            f"<b>Ҳисоб-китоблар якуни ({info['date']} йил ҳолатига):</b><br/>"
            f"• {uzs_conclusion}."
        )

    elements.append(Paragraph(conclusion_text, conclusion_style))
    elements.append(Spacer(1, 14))

    # 5. Signatures (Two Columns)
    sign_data = [
        [
            Paragraph(
                f"<b>«Diyor Group» МЧЖ номидан:</b><br/>"
                f"Раҳбар / Бош ҳисобчи<br/><br/>"
                f"__________________________ (Имзо)<br/>"
                f"<font size='6.5' color='#64748B'>М.П.</font>",
                parties_style,
            ),
            Paragraph(
                f"<b>{info['title']} номидан:</b><br/>"
                f"Раҳбар / Масъул шахс<br/><br/>"
                f"__________________________ (Имзо)<br/>"
                f"<font size='6.5' color='#64748B'>М.П.</font>",
                parties_style,
            ),
        ]
    ]
    sign_table = Table(sign_data, colWidths=[268, 268])
    sign_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(sign_table)

    doc.build(elements)
    buf.seek(0)
    return buf
