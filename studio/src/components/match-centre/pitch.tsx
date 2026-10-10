import type { ReactNode } from "react";

/** A 105 × 68 m pitch in metres; data y runs up from the bottom touchline, SVG y runs down. */
const W = 105;
const H = 68;
const sy = (y: number) => H - y;

export interface Dot {
  id: string;
  x: number;
  y: number;
  color: string;
  label: string;
  highlight?: boolean;
}

function Markings({ color = "rgba(255,255,255,0.75)" }: { color?: string }) {
  const line = { fill: "none", stroke: color, strokeWidth: 0.3 };
  return (
    <g {...line}>
      <rect x={0} y={0} width={W} height={H} />
      <line x1={W / 2} y1={0} x2={W / 2} y2={H} />
      <circle cx={W / 2} cy={H / 2} r={9.15} />
      {[0, W].map((gx) => {
        const dir = gx === 0 ? 1 : -1;
        const box = (depth: number, width: number) => (
          <rect x={gx === 0 ? 0 : W - depth} y={(H - width) / 2} width={depth} height={width} />
        );
        return (
          <g key={gx}>
            {box(16.5, 40.32)}
            {box(5.5, 18.32)}
            <circle cx={gx + dir * 11} cy={H / 2} r={0.25} fill={color} />
            <path d={`M ${gx + dir * 16.5} ${H / 2 - 7.3} A 9.15 9.15 0 0 ${dir === 1 ? 1 : 0} ${gx + dir * 16.5} ${H / 2 + 7.3}`} />
            <rect x={gx === 0 ? -1.8 : W} y={H / 2 - 3.66} width={1.8} height={7.32} strokeWidth={0.25} />
          </g>
        );
      })}
      <circle cx={W / 2} cy={H / 2} r={0.25} fill={color} />
    </g>
  );
}

/** A light pitch for charts (shot map, pass network): marks sit on a quiet surface. */
export function ChartPitch({ children, label }: { children: ReactNode; label: string }) {
  return (
    <svg viewBox="-3 -3 111 74" className="block w-full rounded-md bg-slate-50" role="img" aria-label={label}>
      <Markings color="#cbd5e1" />
      {children}
    </svg>
  );
}

export { sy as svgY };

export function Pitch({
  dots,
  ball,
  children,
  label,
}: {
  dots: Dot[];
  ball: { x: number; y: number; dead?: boolean } | null; // dead: play stopped, drawn faded
  children?: ReactNode;
  label: string;
}) {
  return (
    <div className="relative overflow-hidden rounded-lg bg-emerald-800 shadow-inner">
      <svg viewBox="-3 -3 111 74" className="block w-full" role="img" aria-label={label}>
        {Array.from({ length: 10 }, (_, i) => (
          <rect key={i} x={i * 10.5} y={0} width={10.5} height={H} fill={i % 2 ? "#166534" : "#15803d"} />
        ))}
        <Markings />
        {dots.map((d) => (
          <g key={d.id} transform={`translate(${d.x} ${sy(d.y)})`}>
            {d.highlight && <circle r={3} fill="none" stroke="#fde047" strokeWidth={0.5} />}
            <circle r={1.7} fill={d.color} stroke="white" strokeWidth={0.35} />
            <text
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={1.6}
              fontWeight={700}
              fill="white"
              style={{ pointerEvents: "none" }}
            >
              {d.label}
            </text>
          </g>
        ))}
        {ball && (
          <circle
            cx={ball.x}
            cy={sy(ball.y)}
            r={0.85}
            fill="white"
            stroke="#0f172a"
            strokeWidth={0.3}
            opacity={ball.dead ? 0.45 : 1}
          />
        )}
      </svg>
      {children}
    </div>
  );
}
