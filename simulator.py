#!/usr/bin/env python3
"""Local exercise fixture. Python 3.10+, standard library only; no external calls."""
import argparse
import csv
import json
import secrets
import socket
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT_TOKEN = 'demo-agent-site-01'
SUPPLIER_TOKEN = 'demo-supplier'
TABLES = ('sites', 'invoice_headers', 'invoice_lines', 'pos_daily',
          'inventory_counts', 'vendors', 'vendor_catalog', 'menu', 'recipes',
          'ingredient_mappings', 'notes', 'supplier_documents')


class Failure(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, body TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, reference TEXT NOT NULL UNIQUE, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        db.execute('INSERT OR IGNORE INTO settings VALUES (?, ?)', ('operator_token', secrets.token_urlsafe(24)))


def setting(db, key, default=''):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def set_setting(db, key, value):
    db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (key, value))


def load_rows(table):
    with (ROOT / 'data' / (table + '.csv')).open(newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(',', ':'))


def validate_order(body):
    if not isinstance(body, dict) or set(body) != {'site_id', 'vendor_id', 'lines'}:
        raise Failure(400, 'expected_site_vendor_lines')
    if body['site_id'] not in {'SITE-01', 'SITE-02'} or body['vendor_id'] not in {'V1', 'V2', 'V3'}:
        raise Failure(400, 'unknown_site_or_vendor')
    lines = body['lines']
    if not isinstance(lines, list) or not 1 <= len(lines) <= 20:
        raise Failure(400, 'expected_1_to_20_lines')
    catalog = {(r['vendor_id'], r['sku']): r for r in load_rows('vendor_catalog') if r['site_id'] == body['site_id']}
    seen = set()
    for line in lines:
        if not isinstance(line, dict) or set(line) != {'sku', 'quantity', 'unit'}:
            raise Failure(400, 'invalid_line')
        sku = line['sku']
        if not isinstance(sku, str) or sku in seen or (body['vendor_id'], sku) not in catalog:
            raise Failure(400, 'unknown_or_duplicate_sku')
        seen.add(sku)
        if type(line['quantity']) is not int or not 1 <= line['quantity'] <= 100:
            raise Failure(400, 'quantity_must_be_integer_1_to_100')
        if line['unit'] != catalog[(body['vendor_id'], sku)]['order_unit']:
            raise Failure(400, 'invalid_order_unit')
    return body


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def reply(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.handle_request('GET')

    def do_POST(self):
        self.handle_request('POST')

    def handle_request(self, method):
        try:
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            if any(len(values) != 1 for values in query.values()):
                raise Failure(400, 'repeated_query_parameter')
            query = {key: values[0] for key, values in query.items()}
            body = {}
            if method == 'POST':
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise Failure(400, 'invalid_body_size')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise Failure(400, 'expected_object')
            with sqlite3.connect(self.server.state) as db:
                token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                role = ('operator' if secrets.compare_digest(token, setting(db, 'operator_token')) else
                        'agent' if token == AGENT_TOKEN and self.server.mode == 'agent' else
                        'supplier' if token == SUPPLIER_TOKEN and self.server.mode == 'supplier' else '')
                if parsed.path == '/health' and method == 'GET':
                    result = (200, {'status': 'ok', 'mode': self.server.mode})
                else:
                    if not role:
                        raise Failure(401, 'unauthorized')
                    result = self.route(db, method, parsed.path, query, body, role)
            # Transactions commit before a simulated lost response.
            if result is None:
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
            else:
                self.reply(*result)
        except Failure as error:
            self.reply(error.status, {'error': error.code})
        except (ValueError, TypeError, KeyError):
            self.reply(400, {'error': 'invalid_request'})

    def route(self, db, method, path, query, body, role):
        parts = path.strip('/').split('/')
        if path == '/control/scenario' and method == 'POST':
            if role != 'operator':
                raise Failure(403, 'operator_required')
            name = body.get('name')
            if name not in ('supplier-timeout', 'delivery-correction', 'normal'):
                raise Failure(400, 'unknown_scenario')
            if name == 'normal':
                set_setting(db, 'drop_next', '0')
                set_setting(db, 'delivery_correction', '0')
            elif name == 'supplier-timeout':
                set_setting(db, 'drop_next', '1')
            else:
                set_setting(db, 'delivery_correction', '1')
            return 200, {'scenario': name}

        if self.server.mode == 'agent' and len(parts) == 2 and parts[0] == 'api' and parts[1] in TABLES and method == 'GET':
            site = query.get('site_id', 'SITE-01')
            if site != 'SITE-01' and role != 'operator':
                raise Failure(403, 'site_forbidden')
            if site not in ('SITE-01', 'SITE-02'):
                raise Failure(404, 'site_not_found')
            table = parts[1]
            rows = [r for r in load_rows(table) if r['site_id'] == site]
            if site == 'SITE-01' and setting(db, 'delivery_correction') == '1':
                if table == 'invoice_headers':
                    rows = [dict(r, delivery_date='2026-10-08', status='scheduled', source_revision='2')
                            if r['invoice_id'] == 'INV-099' else r for r in rows]
                if table == 'notes':
                    rows.append({'site_id': site, 'note_id': 'N16', 'recorded_at': '2026-10-05T06:10:00-04:00',
                                 'author': 'Maya', 'text': 'Alder confirmed INV-099 has NOT arrived. Delivery moved to Thursday October 8. The old received status was wrong.',
                                 'source_revision': '1'})
            date_field = {'pos_daily': 'business_date', 'invoice_headers': 'invoice_date',
                          'inventory_counts': 'counted_at', 'notes': 'recorded_at'}.get(table)
            common = {'site_id', 'limit', 'offset'}
            fields = set(rows[0]) if rows else set()
            if set(query) - common - fields - ({'date_from', 'date_to'} if date_field else set()):
                raise Failure(400, 'unknown_filter')
            for key, value in query.items():
                if key not in common | {'date_from', 'date_to'}:
                    rows = [r for r in rows if r.get(key) == value]
            if date_field:
                rows = [r for r in rows if query.get('date_from', '') <= r[date_field][:10] <= query.get('date_to', '9999-12-31')]
            limit, offset = int(query.get('limit', '50')), int(query.get('offset', '0'))
            if not 1 <= limit <= 100 or offset < 0:
                raise Failure(400, 'invalid_pagination')
            page = rows[offset:offset + limit]
            return 200, {'records': page, 'total': len(rows), 'next_offset': offset + limit if offset + limit < len(rows) else None,
                         'source': table + '.csv', 'dataset_revision': 2 if setting(db, 'delivery_correction') == '1' else 1}

        if self.server.mode == 'agent' and path == '/api/drafts' and method == 'POST':
            order = validate_order(body)
            if order['site_id'] != 'SITE-01' and role != 'operator':
                raise Failure(403, 'site_forbidden')
            draft_id = 'D-' + secrets.token_hex(6)
            db.execute('INSERT INTO drafts(id, body) VALUES (?, ?)', (draft_id, canonical(order)))
            return 201, {'draft_id': draft_id, 'status': 'awaiting_approval', 'order': order}

        if self.server.mode == 'agent' and len(parts) in (3, 4) and parts[:2] == ['api', 'drafts']:
            row = db.execute('SELECT body, approved FROM drafts WHERE id=?', (parts[2],)).fetchone()
            if row is None:
                raise Failure(404, 'draft_not_found')
            order = json.loads(row[0])
            if order['site_id'] != 'SITE-01' and role != 'operator':
                raise Failure(403, 'site_forbidden')
            if len(parts) == 4 and parts[3] == 'approve' and method == 'POST':
                if role != 'operator':
                    raise Failure(403, 'operator_required')
                if body != {'approved': True}:
                    raise Failure(400, 'explicit_approval_required')
                db.execute('UPDATE drafts SET approved=1 WHERE id=?', (parts[2],))
                return 200, {'draft_id': parts[2], 'status': 'approved', 'order': order}
            if len(parts) == 3 and method == 'GET':
                return 200, {'draft_id': parts[2], 'status': 'approved' if row[1] else 'awaiting_approval', 'order': order}

        prefix = '/api/orders' if self.server.mode == 'agent' else '/supplier/orders'
        if path == prefix and method == 'POST':
            if self.server.mode == 'agent':
                if set(body) != {'draft_id'} or not isinstance(body['draft_id'], str):
                    raise Failure(400, 'expected_draft_id')
                row = db.execute('SELECT body, approved FROM drafts WHERE id=?', (body['draft_id'],)).fetchone()
                if row is None:
                    raise Failure(404, 'draft_not_found')
                order = json.loads(row[0])
                if order['site_id'] != 'SITE-01' and role != 'operator':
                    raise Failure(403, 'site_forbidden')
                if not row[1]:
                    raise Failure(409, 'approval_required')
                reference = body['draft_id']
            else:
                reference = body.get('client_reference')
                order = validate_order({k: v for k, v in body.items() if k != 'client_reference'})
            if not isinstance(reference, str) or not 1 <= len(reference) <= 128:
                raise Failure(400, 'invalid_client_reference')
            existing = db.execute('SELECT id, body FROM orders WHERE reference=?', (reference,)).fetchone()
            if existing:
                if existing[1] != canonical(order):
                    raise Failure(409, 'reference_payload_conflict')
                return 200, {'order_id': existing[0], 'client_reference': reference, 'status': 'accepted', 'order': order}
            order_id = 'O-' + secrets.token_hex(6)
            db.execute('INSERT INTO orders VALUES (?, ?, ?)', (order_id, reference, canonical(order)))
            if setting(db, 'drop_next') == '1':
                set_setting(db, 'drop_next', '0')
                return None
            return 201, {'order_id': order_id, 'client_reference': reference, 'status': 'accepted', 'order': order}
        if path == prefix and method == 'GET':
            if set(query) - {'client_reference', 'site_id'}:
                raise Failure(400, 'unknown_filter')
            site = query.get('site_id', 'SITE-01')
            if self.server.mode == 'agent' and site != 'SITE-01' and role != 'operator':
                raise Failure(403, 'site_forbidden')
            rows = db.execute('SELECT id, reference, body FROM orders ORDER BY rowid').fetchall()
            orders = [{'order_id': row[0], 'client_reference': row[1], 'status': 'accepted', 'order': json.loads(row[2])}
                      for row in rows if json.loads(row[2])['site_id'] == site and
                      ('client_reference' not in query or query['client_reference'] == row[1])]
            return 200, {'orders': orders}
        raise Failure(404, 'not_found')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['serve', 'approve', 'scenario'])
    parser.add_argument('--mode', choices=['agent', 'supplier'], default='agent')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--state', type=Path, default=ROOT / '.state' / 'simulator.sqlite')
    parser.add_argument('--draft-id')
    parser.add_argument('--name', choices=['normal', 'supplier-timeout', 'delivery-correction'])
    args = parser.parse_args()
    if args.command == 'serve':
        initialize(args.state)
        server = HTTPServer(('127.0.0.1', args.port), Handler)
        server.mode, server.state = args.mode, args.state
        print(f'{args.mode} fixture listening on http://127.0.0.1:{args.port}', flush=True)
        print('Local synthetic data only. Ctrl-C stops; state persists.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    if not args.state.exists():
        parser.error('Start the server first, using the same --state path.')
    with sqlite3.connect(args.state) as db:
        token = setting(db, 'operator_token')
    if args.command == 'approve':
        if not args.draft_id:
            parser.error('--draft-id is required')
        path, body = '/api/drafts/' + urllib.parse.quote(args.draft_id, safe='') + '/approve', {'approved': True}
    else:
        if not args.name:
            parser.error('--name is required')
        path, body = '/control/scenario', {'name': args.name}
    request = urllib.request.Request(f'http://127.0.0.1:{args.port}' + path, data=json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            print(json.dumps(json.load(response), indent=2))
    except urllib.error.HTTPError as error:
        parser.exit(1, error.read().decode() + '\n')


if __name__ == '__main__':
    main()
