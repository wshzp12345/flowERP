"""Personal L08 checks against the candidate's real sales service and test DB."""
import json
import sqlite3
import tempfile
from pathlib import Path

from flowerp import ERPStore, InventoryService, MasterDataService, SalesService
from flowerp.identity import IdentityService, SYSTEM_PRINCIPAL
from flowerp.models import InsufficientStock


def _observe(mode, stock):
    import flowerp.sales
    source = Path(flowerp.sales.__file__).resolve()
    assert source.is_relative_to(Path.cwd().resolve()), f'产品模块逃出候选：{source}'
    with tempfile.TemporaryDirectory(prefix='l08-personal-') as temp:
        store = ERPStore(Path(temp) / 'test.db')
        IdentityService(store).ensure_local_defaults()
        actor = SYSTEM_PRINCIPAL
        master, inventory, sales = MasterDataService(store), InventoryService(store), SalesService(store)
        products = [master.create_product(actor, 'L08-P-' + key, key, 1000, 500) for key in ('A', 'B')]
        customer = master.create_customer(actor, 'L08-P-C', '验收客户', credit_limit_cents=100000)
        for index, product in enumerate(products):
            inventory.receive(actor, product['id'], 'LOC-MAIN-STOCK', stock[index], f'opening-{index}')
        order = sales.create_order(actor, customer['id'], [
            {'product_id': products[0]['id'], 'quantity': 2},
            {'product_id': products[1]['id'], 'quantity': 3},
        ])
        sales.confirm(actor, order['id'])
        tables = ('stock_balance', 'stock_reservations', 'sales_document_lines', 'sales_documents')
        def snapshot():
            return {table: store.rows(f'SELECT * FROM {table} ORDER BY rowid') for table in tables}
        before = snapshot()
        if mode == 'write_error':
            with store.connect() as connection:
                connection.execute("CREATE TRIGGER l08_personal_fault BEFORE INSERT ON stock_reservations "
                                   "WHEN NEW.product_id='" + products[1]['id'].replace("'", "''") + "' "
                                   "BEGIN SELECT RAISE(ABORT,'L08 personal second insert failed'); END")
        error = None
        try:
            sales.reserve(actor, order['id'])
        except (InsufficientStock, sqlite3.IntegrityError) as failure:
            error = {'type': type(failure).__name__, 'message': str(failure)}
        after = snapshot()
        balances = [inventory.balance(actor, item['id'], 'LOC-MAIN-STOCK') for item in products]
        current = sales.order(actor, order['id'])
        reserved = [item['reserved'] for item in balances]
        available = [item['available'] for item in balances]
        if mode == 'success':
            assert error is None, error
            assert current['status'] == 'reserved', current
            assert reserved == [2, 3] and available == [3, 2], balances
            assert len(after['stock_reservations']) == 2, after
            assert [item['quantity'] for item in after['stock_reservations']] == [2, 3], after
            assert [item['reserved_quantity'] for item in after['sales_document_lines']] == [2, 3], after
            assert [item['ordered_quantity'] for item in after['sales_document_lines']] == [2, 3], after
            assert [item['on_hand'] for item in after['stock_balance']] == [5, 5], after
        else:
            assert error is not None, '故障未导致整单拒绝'
            assert error['type'] == ('IntegrityError' if mode == 'write_error' else 'InsufficientStock'), error
            if mode == 'write_error':
                assert 'L08 personal second insert failed' in error['message'], error
            assert before == after, {'before': before, 'after': after, 'error': error}
            assert current['status'] == 'confirmed', current
            assert reserved == [0, 0] and available == stock, balances
        return dict(mode=mode, module_source=str(source), input_stock=stock, requested=[2, 3],
                    status=current['status'], reserved=reserved, available=available, error=error,
                    before=before, after=after, four_tables_unchanged=before == after)


def l08_sales_two_line_success():
    return json.dumps(_observe('success', [5, 5]), ensure_ascii=False)


def l08_sales_second_line_shortage():
    return json.dumps([_observe('shortage', stock) for stock in ([5, 2], [4, 1])], ensure_ascii=False)


def l08_sales_second_write_rollback():
    return json.dumps(_observe('write_error', [5, 5]), ensure_ascii=False)
