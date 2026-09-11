import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merges class name lists, resolving Tailwind class conflicts (e.g. a
 * later `p-4` wins over an earlier `p-2`). Standard shadcn/ui helper --
 * kept here since components.json already points `utils` at this path
 * for components added later via the shadcn CLI.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
