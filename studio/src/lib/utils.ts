import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Match minute as the insight copy writes it, so a card and its text agree: the engine's
 * minute to 0.1, then the nearest whole minute, halves to even (Python's format):
 * 41.6 -> "42'", 40.5 -> "40'".
 */
export function minuteLabel(minute: number): string {
  const tenths = Math.round(minute * 10);
  const whole = Math.floor(tenths / 10);
  const rest = tenths - whole * 10;
  return `${whole + (rest > 5 || (rest === 5 && whole % 2 === 1) ? 1 : 0)}'`;
}

/** Period 2 clock 15:00 is minute 60 (the engine's convention). */
export function matchMinute(period: number, clockMs: number): number {
  return (period === 2 ? 45 : 0) + clockMs / 60000;
}
