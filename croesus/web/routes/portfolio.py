from __future__ import annotations
import json
import tempfile
from pathlib import Path
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection, get_write_connection
from croesus.web.forms import holdings_form_to_csv, parse_transaction_form
from croesus.web.services import (
    build_portfolio_view, resolve_portfolio_id, resolve_symbol_map, opportunity_cache,
)
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot

router = APIRouter()


@router.get("/portfolio", response_class=HTMLResponse)
def portfolio(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_portfolio_view(conn)
    donut = json.dumps([{"name": h["symbol"], "value": h["market_value"] or 0}
                        for h in view.holdings])
    equity = json.dumps(view.history)
    return templates.TemplateResponse(request, "portfolio.html",
        {"title": "포트폴리오", "view": view, "donut_json": donut, "equity_json": equity})


@router.get("/portfolio/edit", response_class=HTMLResponse)
def edit_holdings(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_portfolio_view(conn)
    return templates.TemplateResponse(request, "portfolio_edit.html",
        {"title": "보유 편집", "view": view})


@router.post("/portfolio/holdings", response_class=HTMLResponse)
async def save_holdings(request: Request, db_path=Depends(get_db_path)):
    form = await request.form()
    data = {k: form.getlist(k) for k in ("symbol", "quantity", "avg_cost", "currency", "market_value")}
    csv_text = holdings_form_to_csv(data)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
        tmp.write(csv_text)
        tmp_path = Path(tmp.name)
    try:
        with get_write_connection(db_path) as conn:
            pid = resolve_portfolio_id(conn)
            run_portfolio_snapshot(conn, tmp_path, portfolio_id=pid)
    finally:
        tmp_path.unlink(missing_ok=True)
        opportunity_cache.invalidate()
    return RedirectResponse("/portfolio", status_code=303)


@router.get("/portfolio/edit/row", response_class=HTMLResponse)
def add_holding_row(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/holdings_rows.html", {"h": {}})


# 돈이 들어오는/나가는 거래(원장 표시에서 +/− 흐름과 색을 가른다).
_INFLOW = {"sell", "deposit", "dividend"}
_OUTFLOW = {"buy", "withdrawal", "fee"}


def _fetch_ledger(conn, pid: str) -> list[dict]:
    rows = conn.execute(
        "SELECT transaction_date, transaction_type, asset_id, quantity, price, "
        "currency, gross_amount, fees FROM portfolio_transactions "
        "WHERE portfolio_id = ? ORDER BY transaction_date DESC, transaction_id DESC LIMIT 100",
        [pid]).fetchall()
    symbols = resolve_symbol_map(conn, [r[2] for r in rows if r[2]])
    ledger = []
    for date, ttype, asset_id, qty, price, currency, gross, fees in rows:
        sym, name = symbols.get(asset_id, (None, None)) if asset_id else (None, None)
        amount = gross
        if amount is None and qty is not None and price is not None:
            amount = qty * price
        ledger.append({
            "date": str(date), "type": ttype, "asset_id": asset_id,
            "symbol": sym or asset_id, "name": name,
            "quantity": qty, "price": price, "currency": currency or "USD",
            "amount": amount, "fees": fees or 0.0,
            "flow": "in" if ttype in _INFLOW else "out" if ttype in _OUTFLOW else None,
        })
    return ledger


@router.get("/portfolio/transactions", response_class=HTMLResponse)
def transactions(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        pid = resolve_portfolio_id(conn)
        ledger = _fetch_ledger(conn, pid)
    return templates.TemplateResponse(request, "transactions.html",
        {"title": "거래 원장", "ledger": ledger, "errors": []})


@router.post("/portfolio/transactions", response_class=HTMLResponse)
async def add_transaction(request: Request, db_path=Depends(get_db_path)):
    form = await request.form()
    data = {k: form.get(k) for k in form.keys()}
    with get_read_connection(db_path) as conn:
        pid = resolve_portfolio_id(conn)
    txn, errors = parse_transaction_form(data, pid)
    if errors:
        with get_read_connection(db_path) as conn:
            err_pid = resolve_portfolio_id(conn)
            ledger = _fetch_ledger(conn, err_pid)
        return templates.TemplateResponse(request, "transactions.html",
            {"title": "거래 원장", "ledger": ledger, "errors": errors}, status_code=400)
    with get_write_connection(db_path) as conn:
        TransactionRepository(conn).record_transaction(txn)
    return RedirectResponse("/portfolio/transactions", status_code=303)
