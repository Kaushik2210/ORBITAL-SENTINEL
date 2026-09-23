"use client";

import { useEffect, useRef } from "react";
import uPlot from "uplot";

export interface ChartSeries {
  label: string;
  color: string;
  values: (number | null)[];
}

/** A thin uPlot wrapper for streaming telemetry. Recreated when the set of series changes
 * (a channel added/removed), otherwise just fed new data — recreating a chart on every sample
 * would both be slow and lose zoom/cursor state. */
export function TelemetryChart({
  x,
  series,
  height = 220,
}: {
  x: number[];
  series: ChartSeries[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const labelsKey = series.map((s) => s.label).join("|");

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const opts: uPlot.Options = {
      width: el.clientWidth || 600,
      height,
      series: [
        { label: "t" },
        ...series.map((s) => ({ label: s.label, stroke: s.color, width: 1.5 })),
      ],
      axes: [
        { stroke: "#8b93a7", grid: { stroke: "#1f2937", width: 1 }, ticks: { stroke: "#1f2937" } },
        { stroke: "#8b93a7", grid: { stroke: "#1f2937", width: 1 }, ticks: { stroke: "#1f2937" } },
      ],
      scales: { x: { time: false } },
      legend: { show: series.length > 0 },
      cursor: { show: true },
    };
    const data = [x, ...series.map((s) => s.values)] as uPlot.AlignedData;
    const plot = new uPlot(opts, data, el);
    plotRef.current = plot;

    const onResize = () => plot.setSize({ width: el.clientWidth, height });
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      plot.destroy();
      plotRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labelsKey, height]);

  useEffect(() => {
    plotRef.current?.setData([x, ...series.map((s) => s.values)] as uPlot.AlignedData);
  }, [x, series]);

  return <div ref={containerRef} className="w-full [&_.u-legend]:text-xs" />;
}
