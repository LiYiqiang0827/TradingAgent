"""Equity and drawdown charts for the frozen 300k compounding replay."""
from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'outputs/core_reactivation_compound_300k_2026'


def main():
    results = json.loads((OUT/'RESULTS.json').read_text())['scenarios']
    fig, axes = plt.subplots(2, 2, figsize=(15, 9), sharex=True, sharey='row', layout='constrained',
                             gridspec_kw={'height_ratios': [2, 1]})
    weights = [('0.05', '5%', '#2376ab'), ('0.10', '10%', '#8552a3'), ('0.20', '20%', '#d06928')]
    modes = [('minute_old', 'Multi-day exit'),
             ('minute_carry', 'Carry invalidation to earliest sale')]
    for col, (mode, title) in enumerate(modes):
        for weight, label, color in weights:
            key = f'{mode}_w{weight}'
            data = pd.read_csv(OUT/f'{key}_minute_nav.csv')
            stamps = pd.to_datetime(data.time)
            nav = pd.to_numeric(data.nav)
            dd = (nav / nav.cummax() - 1) * 100
            end = results[key]['final_capital']
            axes[0, col].plot(stamps, nav/1000, color=color, lw=1.4,
                             label=f'{label} budget: final RMB {float(end):,.0f}')
            axes[1, col].plot(stamps, dd, color=color, lw=1.1,
                             label=f'{label}: MDD {results[key]["minute_drawdown"]["max_drawdown_pct"]:.2f}%')
        axes[0, col].axhline(300, color='#777', ls='--', lw=.8)
        axes[0, col].set_title(title, fontsize=13)
        axes[0, col].set_ylabel('Account value (RMB thousands)')
        axes[1, col].set_ylabel('Drawdown from peak (%)')
        axes[1, col].axhline(0, color='#777', lw=.8)
        for row in range(2):
            ax = axes[row, col]
            ax.grid(alpha=.2)
            ax.legend(fontsize=9, loc='best', framealpha=.95)
            ax.xaxis.set_major_locator(mdates.MonthLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    fig.suptitle('RMB 300,000 compounding replay | 2026-06-01 to 2026-09-24\n'
                 'Daily budget = prior close NAV x allocation | synchronized minute-close mark-to-market', fontsize=15)
    fig.supxlabel('Includes frozen execution costs, 100-share lots and cash constraints. Historical development sample; proxy fills.\n'
                  'Drawdown uses minute closes, not tick/intraminute extremes. No claim of stable future profitability.', fontsize=10)
    fig.savefig(OUT/'compound_300k_equity_drawdown.jpg', dpi=160)
    fig.savefig(OUT/'compound_300k_equity_drawdown.png', dpi=150)
    plt.close(fig)


if __name__ == '__main__':
    main()
