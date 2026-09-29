"""Render fixed cash stress scenarios, explicitly without market NAV claims."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
from datetime import datetime

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'outputs/core_reactivation_account_risk'


def main():
    data = json.loads((OUT / 'CASH_REPLAY.json').read_text())
    scenes = data['scenarios']
    fig, axes = plt.subplots(3, 1, figsize=(13, 11), layout='constrained')
    modes = [('minute_old', 'Multi-day exit', '#236ca9'),
             ('minute_carry', 'Carry invalidation to earliest sale', '#bd6033')]
    labels = ['5%', '10%', '20%']
    for offset, (mode, label, color) in zip([-.18, .18], modes):
        values = [scenes[f'{mode}_w{w}_min5']['total_return_pct_if_complete']
                  for w in ['0.05', '0.10', '0.20']]
        bars = axes[0].bar([i + offset for i in range(3)], values,
                          width=.34, color=color, label=label)
        axes[0].bar_label(bars, fmt='%+.2f%%', padding=3)
    axes[0].set_xticks(range(3), labels)
    axes[0].axhline(0, color='#555', lw=.8)
    axes[0].margins(y=.2)
    axes[0].set_ylabel('Final cash return (%)')
    axes[0].set_title('Fixed initial-budget scenarios: all results, no selection of best configuration')
    axes[0].legend(loc='upper left')
    for mode, label, color in modes:
        scene = scenes[f'{mode}_w0.20_min5']
        # Combine same-time updates: plotted state is after all timestamp events.
        grouped = {r['time']: r for r in scene['timeline']}
        rows = list(grouped.values())
        times = [datetime.fromisoformat(r['time']) for r in rows]
        axes[1].step(times, [float(r['held_at_cost']) / 1000 for r in rows],
                     where='post', label=label, color=color)
        axes[2].step(times, [r['positions'] for r in rows], where='post', label=label, color=color)
    axes[1].set_title('20% budget: outstanding purchase cost (not marked-to-market value)')
    axes[1].set_ylabel('Cost / initial capital (%)')
    axes[2].set_title('20% budget: accepted concurrent positions after lot and cash constraints')
    axes[2].set_ylabel('Positions')
    for ax in axes:
        ax.grid(alpha=.2)
    for ax in axes[1:]:
        ax.xaxis.set_major_formatter(DateFormatter('%m-%d'))
    fig.suptitle('2026 Jun-Sep core reactivation: RMB 100,000 cash stress replay\n'
                 '51 frozen signals | 100-share lots | min commission RMB 5/side | no same-time sale reuse', fontsize=14)
    fig.supxlabel('Development sample; proxy fills and mixed-origin history. No NAV drawdown or safe-position inference.', fontsize=10)
    fig.savefig(OUT / 'cash_stress_review.jpg', dpi=150)
    plt.close(fig)


if __name__ == '__main__':
    main()
