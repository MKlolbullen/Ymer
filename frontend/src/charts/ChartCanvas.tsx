import {
  ArcElement,
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  DoughnutController,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PieController,
  PointElement,
  Tooltip,
  type ChartConfiguration,
} from "chart.js";
import { useEffect, useRef, useState } from "react";

import { usePalette } from "@/lib/appearance";
import type { ChartConfig, ChartData } from "@/lib/types";

import { isPie } from "./config";
import { CHART_INK, optionColor, seriesColor, type Theme } from "./palette";

Chart.register(
  ArcElement,
  BarController,
  BarElement,
  CategoryScale,
  DoughnutController,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PieController,
  PointElement,
  Tooltip,
);

const FONT = { family: "Inter, ui-sans-serif, system-ui, sans-serif", size: 12 };

/** The Chart.js configuration for grouped data (exported for tests). */
export function chartJsConfig(data: ChartData, config: ChartConfig, theme: Theme, ink = CHART_INK[theme]): ChartConfiguration {
  const pie = isPie(config.kind);
  const multi = data.series.length > 1;
  // One series coloured by its x groups (select options, or slots for pies); several
  // series each take their own colour.
  const perGroup = (i: number) => optionColor(theme, data.colors?.[i], i);

  if (pie) {
    // A pie shows one measure: several series are summed per slice.
    const totals = data.labels.map((_, i) => data.series.reduce((sum, s) => sum + (s.data[i] ?? 0), 0));
    return {
      type: config.kind === "doughnut" ? "doughnut" : "pie",
      data: {
        labels: data.labels,
        datasets: [
          {
            label: data.series[0]?.name ?? "",
            data: totals,
            backgroundColor: data.labels.map((_, i) => perGroup(i)),
            borderColor: ink.surface,
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { position: "right", labels: { color: ink.text, font: FONT, boxWidth: 10, boxHeight: 10 } },
          tooltip: { bodyFont: FONT, titleFont: FONT },
        },
      },
    };
  }

  const line = config.kind === "line" || config.kind === "area";
  const horizontal = config.kind === "horizontal_bar";
  const stacked = multi && config.stacked && !line;
  const datasets = data.series.map((s, i) => {
    const color = multi ? optionColor(theme, s.color, i) : seriesColor(theme, 0);
    const colors = !multi && data.colors ? data.labels.map((_, j) => perGroup(j)) : color;
    return line
      ? {
          label: s.name,
          data: s.data,
          borderColor: color,
          backgroundColor: config.kind === "area" ? `${color}33` : color,
          fill: config.kind === "area" ? (multi ? "origin" : true) : false,
          borderWidth: 2,
          pointRadius: data.labels.length > 40 ? 0 : 3,
          pointHoverRadius: 5,
          tension: 0.25,
          spanGaps: true,
        }
      : {
          label: s.name,
          data: s.data,
          backgroundColor: colors,
          borderColor: ink.surface,
          // 2px surface gap between stacked segments and neighbouring bars.
          borderWidth: stacked ? { top: 2 } : 0,
          borderRadius: 4,
          borderSkipped: "start" as const,
          maxBarThickness: 48,
          categoryPercentage: 0.8,
          barPercentage: 0.9,
        };
  });

  const valueAxis = {
    beginAtZero: true,
    stacked,
    grid: { color: ink.grid },
    border: { display: false },
    ticks: { color: ink.muted, font: FONT, precision: config.aggregate === "count" ? 0 : undefined },
  };
  const groupAxis = {
    stacked,
    grid: { display: false },
    border: { color: ink.grid },
    ticks: { color: ink.text, font: FONT, autoSkip: true, maxRotation: 45 },
  };
  return {
    type: line ? "line" : "bar",
    data: { labels: data.labels, datasets: datasets as never },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      indexAxis: horizontal ? "y" : "x",
      interaction: { mode: "index", intersect: false },
      scales: horizontal ? { x: valueAxis, y: groupAxis } : { x: groupAxis, y: valueAxis },
      plugins: {
        // A single series is named by the chart's caption; several need a legend.
        legend: { display: multi, position: "top", align: "start", labels: { color: ink.text, font: FONT, boxWidth: 10, boxHeight: 10 } },
        tooltip: { bodyFont: FONT, titleFont: FONT },
      },
    },
  } as ChartConfiguration;
}

function canDraw(): boolean {
  try {
    return !!document.createElement("canvas").getContext("2d");
  } catch {
    return false;
  }
}

/** Draws grouped chart data with Chart.js, redrawing when the data, config or theme change. */
export function ChartCanvas({ data, config, height = 320, label }: { data: ChartData; config: ChartConfig; height?: number; label: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const palette = usePalette();
  const [supported] = useState(canDraw);

  useEffect(() => {
    if (!supported || !canvasRef.current) return;
    const theme = palette === "light" ? "light" : "dark";
    let ink = CHART_INK[theme];
    if (palette !== "light" && palette !== "dark") {
      const styles = getComputedStyle(document.documentElement);
      const color = (step: number) => styles.getPropertyValue(`--color-zinc-${step}`).trim();
      ink = { text: color(100), muted: color(400), grid: color(700), surface: color(950) };
    }
    const next = chartJsConfig(data, config, theme, ink);
    const chart = chartRef.current;
    if (chart && (chart.config as { type?: string }).type === next.type) {
      chart.data = next.data;
      chart.options = next.options ?? {};
      chart.update("none");
      return;
    }
    chart?.destroy();
    chartRef.current = new Chart(canvasRef.current, next);
  }, [data, config, palette, supported]);

  useEffect(
    () => () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    },
    [],
  );

  return (
    <div style={{ height }} className="relative">
      {supported ? <canvas ref={canvasRef} role="img" aria-label={label} /> : <p className="text-sm text-zinc-500">Charts need a browser with canvas support.</p>}
    </div>
  );
}
