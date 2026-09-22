"""Excel Exporter service using openpyxl for Diyorgroup multi-agent platform."""
import io
import structlog
from datetime import datetime
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from models.crm import CrmDeal
from models.product import MdmProduct
from models.debt import DebtRegistry

logger = structlog.get_logger(__name__)

HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")
TITLE_FONT = Font(name="Calibri", size=14, bold=True, color="1B365D")
SUBTITLE_FONT = Font(name="Calibri", size=10, italic=True, color="555555")
BORDER_THIN = Border(
    left=Side(style="thin", color="DDDDDD"),
    right=Side(style="thin", color="DDDDDD"),
    top=Side(style="thin", color="DDDDDD"),
    bottom=Side(style="thin", color="DDDDDD")
)

def auto_fit_columns(ws):
    """Adjust column widths based on content."""
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val = str(cell.value or "")
            if len(val) > max_len:
                max_len = len(val)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

async def generate_excel_report(session: AsyncSession) -> io.BytesIO:
    """
    Generates a rich multi-sheet Excel workbook with live data:
    1. CRM Deals (Сделки)
    2. MoySklad Stock (Остатки склада)
    3. Debt Registry & Hard-Lock (Задолженность и блокировки)
    """
    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    # ----------------------------------------------------
    # Sheet 1: Сделки CRM
    # ----------------------------------------------------
    ws_deals = wb.create_sheet(title="Сделки CRM")
    ws_deals.views.sheetView[0].showGridLines = True
    
    ws_deals["A1"] = "Diyor Group • Реестр Сделок CRM"
    ws_deals["A1"].font = TITLE_FONT
    ws_deals["A2"] = f"Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} • Источник: FastAPI Native CRM"
    ws_deals["A2"].font = SUBTITLE_FONT

    deal_headers = [
        "#", "ID Сделки", "Название заказа", "Стадия", 
        "Контрагент", "Телефон", "Дизайнер", 
        "Сумма сделки (UZS)", "Комиссия 5% (UZS)", "Заказ МойСклад", "Дата создания"
    ]
    ws_deals.append([]) # Row 3 blank
    ws_deals.append(deal_headers) # Row 4 headers

    for col_idx in range(1, len(deal_headers) + 1):
        cell = ws_deals.cell(row=4, column=col_idx)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    stmt_deals = select(CrmDeal).order_by(desc(CrmDeal.created_at))
    res_deals = await session.execute(stmt_deals)
    deals = res_deals.scalars().all()

    for idx, d in enumerate(deals, 1):
        created_str = d.created_at.strftime("%d.%m.%Y %H:%M") if d.created_at else "-"
        row_data = [
            idx,
            str(d.id),
            d.title,
            d.stage.value if hasattr(d.stage, 'value') else str(d.stage),
            d.counterparty_name or "-",
            d.counterparty_phone or "-",
            d.designer_username or "-",
            d.total_amount or 0.0,
            d.designer_commission or 0.0,
            d.moysklad_order_id or "-",
            created_str
        ]
        ws_deals.append(row_data)
        curr_row = ws_deals.max_row
        for col_idx in range(1, len(row_data) + 1):
            c = ws_deals.cell(row=curr_row, column=col_idx)
            c.border = BORDER_THIN
            if col_idx in [8, 9]: # Numeric columns
                c.number_format = '#,##0.00'
                c.alignment = Alignment(horizontal="right")
            elif col_idx == 1:
                c.alignment = Alignment(horizontal="center")

    auto_fit_columns(ws_deals)

    # ----------------------------------------------------
    # Sheet 2: Остатки склада МойСклад
    # ----------------------------------------------------
    ws_stock = wb.create_sheet(title="Склад МойСклад")
    ws_stock.views.sheetView[0].showGridLines = True

    ws_stock["A1"] = "Diyor Group • Складские Остатки МойСклад (Live Sync)"
    ws_stock["A1"].font = TITLE_FONT
    ws_stock["A2"] = f"Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} • 5000+ товаров"
    ws_stock["A2"].font = SUBTITLE_FONT

    stock_headers = [
        "#", "Артикул / SKU", "Наименование товара", "Категория / Бренд",
        "Свободный остаток (шт)", "В резерве (шт)", 
        "Цена закупки (UZS)", "Розничная цена (UZS)", "Сумма склада (UZS)"
    ]
    ws_stock.append([])
    ws_stock.append(stock_headers)

    for col_idx in range(1, len(stock_headers) + 1):
        cell = ws_stock.cell(row=4, column=col_idx)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    stmt_prod = select(MdmProduct).order_by(desc(MdmProduct.stock_free)).limit(500)
    res_prod = await session.execute(stmt_prod)
    products = res_prod.scalars().all()

    for idx, p in enumerate(products, 1):
        free_qty = p.stock_free or 0
        res_qty = p.stock_reserved or 0
        ret_price = float(p.retail_price or 0.0)
        total_val = free_qty * ret_price
        
        row_data = [
            idx,
            p.sku or "-",
            p.name,
            p.brand or "-",
            free_qty,
            res_qty,
            float(p.purchase_price or 0.0),
            ret_price,
            total_val
        ]
        ws_stock.append(row_data)
        curr_row = ws_stock.max_row
        for col_idx in range(1, len(row_data) + 1):
            c = ws_stock.cell(row=curr_row, column=col_idx)
            c.border = BORDER_THIN
            if col_idx in [5, 6]:
                c.number_format = '#,##0'
                c.alignment = Alignment(horizontal="right")
            elif col_idx in [7, 8, 9]:
                c.number_format = '#,##0.00'
                c.alignment = Alignment(horizontal="right")
            elif col_idx == 1:
                c.alignment = Alignment(horizontal="center")

    auto_fit_columns(ws_stock)

    # ----------------------------------------------------
    # Sheet 3: Задолженность и Hard-Lock (CFO Agent)
    # ----------------------------------------------------
    ws_debt = wb.create_sheet(title="Дебиторская задолженность")
    ws_debt.views.sheetView[0].showGridLines = True

    ws_debt["A1"] = "Diyor Group • Реестр Долгов и Hard-Lock (AI CFO Agent)"
    ws_debt["A1"].font = TITLE_FONT
    ws_debt["A2"] = f"Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} • Правило блокировки: долг > 60 дней"
    ws_debt["A2"].font = SUBTITLE_FONT

    debt_headers = [
        "#", "Контрагент", "MoySklad ID", 
        "Текущий долг 0-30 дней (UZS)", "Долг 30-60 дней (UZS)", 
        "Просроченный долг 60+ дней (UZS)", "Статус отгрузок"
    ]
    ws_debt.append([])
    ws_debt.append(debt_headers)

    for col_idx in range(1, len(debt_headers) + 1):
        cell = ws_debt.cell(row=4, column=col_idx)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    stmt_debts = select(DebtRegistry).order_by(desc(DebtRegistry.debt_60_plus))
    res_debts = await session.execute(stmt_debts)
    debts = res_debts.scalars().all()

    for idx, d in enumerate(debts, 1):
        status_str = "🛑 ЗАБЛОКИРОВАН (Hard-Lock)" if d.is_blocked else "✅ Отгрузки разрешены"
        row_data = [
            idx,
            d.company_title,
            d.company_moysklad_id,
            float(d.debt_0_30 or 0.0),
            float(d.debt_30_60 or 0.0),
            float(d.debt_60_plus or 0.0),
            status_str
        ]
        ws_debt.append(row_data)
        curr_row = ws_debt.max_row
        for col_idx in range(1, len(row_data) + 1):
            c = ws_debt.cell(row=curr_row, column=col_idx)
            c.border = BORDER_THIN
            if col_idx in [4, 5, 6]:
                c.number_format = '#,##0.00'
                c.alignment = Alignment(horizontal="right")
            elif col_idx == 7:
                c.alignment = Alignment(horizontal="center")
                if d.is_blocked:
                    c.font = Font(name="Calibri", size=10, bold=True, color="CC0000")
                else:
                    c.font = Font(name="Calibri", size=10, color="008800")
            elif col_idx == 1:
                c.alignment = Alignment(horizontal="center")

    auto_fit_columns(ws_debt)

    # Save to memory stream
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output
