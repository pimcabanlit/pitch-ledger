import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Match minute as shown on screen: 68.86 -> "68'". */
export function minuteLabel(minute: number): string {
  return `${Math.floor(minute)}'`;
}

/** Period 2 clock 15:00 is minute 60 (the engine's convention). */
export function matchMinute(period: number, clockMs: number): number {
  return (period === 2 ? 45 : 0) + clockMs / 60000;
}
