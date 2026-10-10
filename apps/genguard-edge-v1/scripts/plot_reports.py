"""Optional report plots. Requires Matplotlib, not needed on the UNO Q runtime."""
import csv
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/genguard-edge-mpl")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
font = Path("/System/Library/Fonts/AppleSDGothicNeo.ttc")
if font.exists():
    font_manager.fontManager.addfont(str(font))
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font)).get_name()
plt.rcParams.update({"axes.unicode_minus": False, "axes.spines.top": False,
                     "axes.spines.right": False, "font.size": 11, "figure.facecolor": "#f6f8fb",
                     "axes.facecolor": "#ffffff"})


def main():
    path = ROOT / "verification/demo-final"
    rows = list(csv.DictReader((path / "traces.csv").open()))
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, layout="constrained")
    selected = [r for r in rows if r["scenario"] == "thermal_pressure" and r["model"] == "fusion"]
    t = [float(r["elapsed_s"]) for r in selected]
    axes[0].plot(t, [float(r["coolant_c"]) for r in selected], color="#d96445", label="냉각수 온도 (모의)")
    twin = axes[0].twinx()
    twin.plot(t, [float(r["oil_bar"]) for r in selected], color="#517ab5", label="오일 압력 (모의)")
    axes[0].set_ylabel("온도 (°C)")
    twin.set_ylabel("압력 (bar)")
    axes[0].legend(loc="upper left"); twin.legend(loc="lower left")
    axes[0].set_title("모의 통합 시험 | 진동은 그대로, 온도와 압력만 변화", loc="left", weight="bold")
    for name, label, color in [("fusion", "컨트롤러 + IMU 통합 모델", "#008c82"), ("vibration", "진동 특징만 분석", "#8393a8")]:
        rr = [r for r in rows if r["scenario"] == "thermal_pressure" and r["model"] == name]
        axes[1].plot([float(r["elapsed_s"]) for r in rr], [float(r["score"]) / float(r["threshold"]) for r in rr], label=label, color=color)
    axes[1].axhline(1, ls="--", color="#bd7535", label="모델별 변화 기준")
    axes[1].set(xlabel="모의 운전 경과 (초)", ylabel="변화 점수 / 기준값")
    axes[1].legend(loc="upper left")
    for ax in axes:
        ax.axvline(240, ls=":", color="#d96445")
        ax.grid(alpha=.15)
    axes[1].annotate("240초 변화 주입 → 262초 지속 경보\n실제 발전기 탐지 지연을 뜻하지 않음", xy=(262, 1.3), xytext=(315, 4), arrowprops={"arrowstyle": "->", "color": "#008c82"})
    fig.savefig(path / "demo-comparison.png", dpi=170)
    plt.close(fig)

    path = ROOT / "verification/public-final"
    rows = list(csv.DictReader((path / "traces.csv").open()))
    report = json.loads((path / "report.json").read_text())
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), layout="constrained")
    titles = ["터빈 열화 · 부하 85%", "냉각 펌프 캐비테이션 · 부하 60%", "냉각 펌프 캐비테이션 · 부하 85%"]
    for ax, trial, title in zip(axes, report["test"], titles):
        rr = [r for r in rows if r["source_file"] == trial["source_file"] and r["score"]]
        x = [float(r["elapsed_s"]) / 60 for r in rr]
        y = [float(r["score"]) / float(r["threshold"]) for r in rr]
        ax.plot(x, y, lw=.7, color="#587497", label="실제 공개 엔진 기록")
        ax.axhline(1, ls="--", color="#ce7750", label="경보 기준")
        ax.axvline(trial["fault_intervention_s"] / 60, color="#ad4141", ls=":", label="고장 개입 시점")
        ax.set(title=title + " — 지속 경보 없음", ylabel="점수 / 기준", ylim=(0, 1.2))
        ax.grid(alpha=.15)
    axes[0].legend(loc="upper right", ncol=3, fontsize=9)
    axes[-1].set_xlabel("원본 실험 경과 (분)")
    fig.suptitle("실제 공개 데이터의 한계 | 평가 고장 3개 모두 미탐지\nIMU 없는 선박 엔진 자료 · 운영용 모델로 승인하지 않음", weight="bold", fontsize=14)
    fig.savefig(path / "public-evaluation.png", dpi=170)
    plt.close(fig)


if __name__ == "__main__":
    main()
