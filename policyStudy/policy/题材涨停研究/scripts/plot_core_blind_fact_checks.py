"""Two numerical audit examples from frozen public packets; no outcome access."""
from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np

from derive_core_blind_facts import derive

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'outputs/core_pullback_blind_2025'


def packet(sid):
    return json.loads((OUT / 'packets' / f'{sid}.json').read_text())


def main():
    fig = plt.figure(figsize=(14, 10), layout='constrained')
    gs = fig.add_gridspec(2, 2, height_ratios=[1.3, 1])
    ax = fig.add_subplot(gs[0, :])
    p = packet('S07'); facts = derive(p)['facts']
    rows = [dict(zip(p['m15_columns'], row)) for row in p['m15_bars'] if row[1] in ['D-1', 'D0']]
    first = next(i for i, row in enumerate(rows) if row['day'] == 'D0')
    previous_low = facts['first_m15_low_vs_previous_day_low']['value']['previous_day_low']
    ax.axvspan(first-.5, len(rows)-.5, color='#edf3f8')
    for i, row in enumerate(rows):
        color = '#c74949' if row['close'] >= row['open'] else '#26816f'
        ax.vlines(i, row['low'], row['high'], color=color, lw=1)
        ax.add_patch(Rectangle((i-.3, min(row['open'], row['close'])), .6,
                              max(abs(row['close']-row['open']), .008), color=color))
    ax.axhline(previous_low, color='#586476', ls='--', label=f'Previous day low: {previous_low:.2f}')
    low_index = min(range(first, len(rows)), key=lambda i: rows[i]['low'])
    ax.scatter([first, low_index], [rows[first]['low'], rows[low_index]['low']],
               color=['#265cad', '#ad3f2d'], s=55, zorder=5)
    ax.annotate(f"First D0 bar low: {rows[first]['low']:.2f}\nABOVE previous day low",
                (first, rows[first]['low']), xytext=(first-5, 86.2),
                arrowprops={'arrowstyle': '->', 'color': '#265cad'}, color='#265cad', fontsize=11)
    ax.annotate(f"Later D0 low: {rows[low_index]['low']:.2f}\nBELOW previous day low",
                (low_index, rows[low_index]['low']), xytext=(low_index+2, 80.1),
                arrowprops={'arrowstyle': '->', 'color': '#ad3f2d'}, color='#ad3f2d', fontsize=11)
    ticks = [0, 8, first, first+4, len(rows)-1]
    ax.set(xticks=ticks, xticklabels=[f"{rows[i]['day']} {rows[i]['bar_end']}" for i in ticks],
           ylabel='Price normalized to anchor close = 100',
           title='S07 | The referenced low and the later session low are different observations')
    ax.legend(loc='upper right')

    p = packet('S04'); facts = derive(p)['facts']
    bars = [dict(zip(p['m15_columns'], row)) for row in p['m15_bars']]
    clock = p['decision']['bar_end']
    d0_clocks = {r['bar_end'] for r in bars if r['day'] == 'D0'}
    days = ['D-5', 'D-4', 'D-3', 'D-2', 'D-1', 'D0']
    same_bar = [next(r['volume_ratio'] for r in bars if r['day'] == d and r['bar_end'] == clock) for d in days]
    cumulative = [sum(r['volume_ratio'] for r in bars if r['day'] == d and r['bar_end'] in d0_clocks) for d in days]
    panels = [
        (same_bar, f'S04 | Same {clock} bar across days',
         facts['last_m15_volume_vs_five_prior_same_clock_median']['value']),
        (cumulative, f'S04 | Matched clock slots through {clock}',
         facts['matched_clock_cumulative_volume_ratio']['value']),
    ]
    for j, (values, title, multiple) in enumerate(panels):
        a = fig.add_subplot(gs[1, j]); baseline = float(np.median(values[:-1]))
        a.bar(days, values, color=['#b1bccc']*5 + ['#265cad'], width=.6)
        a.axhline(baseline, color='#d59023', ls='--', label='Prior five-day median')
        for i, v in enumerate(values):
            a.text(i, v+max(values)*.025, f'{v:.3f}', ha='center', fontsize=10)
        a.set(ylim=(0, max(values)*1.23), title=f'{title}\nD0 / prior median = {multiple:.2f}x',
              ylabel='Volume / prior 20-day median daily volume')
        a.legend(loc='upper left', fontsize=9)
    for a in fig.axes:
        a.grid(axis='y', alpha=.15)
        a.spines[['top', 'right']].set_visible(False)
    fig.suptitle('Blind reading audit | Check arithmetic, time and volume duration first', fontsize=18)
    fig.supxlabel('Only frozen pre-decision inputs shown. These facts do not determine future returns or prove a tradable setup.', fontsize=11)
    target = OUT / 'fact_check_examples.jpg'
    fig.savefig(target, dpi=160, facecolor='white')
    print(target)


if __name__ == '__main__':
    main()
