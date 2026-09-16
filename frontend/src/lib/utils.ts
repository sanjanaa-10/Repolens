import { clsx, type ClassValue } from "clsx";

/** Merge Tailwind classes with wx-style semantics. */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}

/** Format a number with thousands separators (1,927). */
export function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}