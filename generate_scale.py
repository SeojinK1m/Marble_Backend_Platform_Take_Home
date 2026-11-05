#!/usr/bin/env python3
"""Create the fixed 100,000-sale load fixture. Python standard library only."""
import csv
import datetime
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def write(path, rows, fields=None):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    output = ROOT / 'scale_data'
    output.mkdir(exist_ok=True)
    for file in sorted((ROOT / 'data').glob('*.csv')):
        if file.name == 'pos_daily.csv':
            continue
        with file.open(newline='', encoding='utf-8') as f:
            base = [r for r in csv.DictReader(f) if r['site_id'] == 'SITE-01']
        rows = []
        for n in range(1, 21):
            for row in base:
                copy = dict(row, site_id=f'SITE-{n:02}')
                if file.name == 'sites.csv':
                    copy['name'] = f'Load fixture restaurant {n:02}'
                rows.append(copy)
        write(output / file.name, rows)
    rng = random.Random(20261005)
    with (ROOT / 'data' / 'menu.csv').open(newline='', encoding='utf-8') as f:
        menu = {r['pos_item_id']: r for r in csv.DictReader(f) if r['site_id'] == 'SITE-01'}
    fields = ['site_id', 'source_revision', 'row_id', 'business_date', 'pos_item_id', 'item_name',
              'quantity_sold', 'gross_sales', 'discounts', 'net_sales']
    with (output / 'pos_daily.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for n in range(100000):
            day = datetime.date(2026, 9, 14) + datetime.timedelta(days=rng.randrange(21))
            choices = ['M01', 'M02', 'M03', 'M04']
            if day >= datetime.date(2026, 9, 26) and day.weekday() >= 5:
                choices += ['M05', 'M99']
            item = menu[rng.choice(choices)]
            writer.writerow(dict(site_id=f'SITE-{rng.randrange(1, 21):02}', source_revision=1,
                row_id=f'SALE-{n:06}', business_date=day.isoformat(), pos_item_id=item['pos_item_id'],
                item_name=item['name'], quantity_sold=1, gross_sales=f"{float(item['menu_price']):.2f}",
                discounts='0.00', net_sales=f"{float(item['menu_price']):.2f}"))
    print('Wrote 100,000 sales rows and supporting records to scale_data/. Seed: 20261005.')


if __name__ == '__main__':
    main()
