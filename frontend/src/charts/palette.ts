/**
 * Chart colours. Series take the categorical slots in a fixed order (never cycled past
 * the end: a ninth series reuses slot colours only because the server caps groups, and
 * the legend still names each). Light and dark are separate, validated steps of the
 * same eight hues, not an automatic flip.
 */
const CATEGORICAL = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"],
};

/** Select options keep their own colour on charts too (colour follows the entity). */
const OPTION_HEX: Record<"light" | "dark", Record<string, string>> = {
  light: {
    gray: "#8a8984",
    brown: "#9a6a3a",
    orange: "#eb6834",
    yellow: "#eda100",
    green: "#1baf7a",
    blue: "#2a78d6",
    purple: "#6250d6",
    pink: "#e87ba4",
    red: "#e34948",
  },
  dark: {
    gray: "#8f8e88",
    brown: "#b07d4a",
    orange: "#d95926",
    yellow: "#c98500",
    green: "#199e70",
    blue: "#3987e5",
    purple: "#9085e9",
    pink: "#d55181",
    red: "#e66767",
  },
};

export type Theme = "light" | "dark";

export const CHART_INK = {
  light: { text: "#52514e", muted: "#8a8984", grid: "#e7e6e2", surface: "#ffffff" },
  dark: { text: "#c3c2b7", muted: "#8f8e88", grid: "#2e2e2c", surface: "#09090b" },
};

export function seriesColor(theme: Theme, index: number): string {
  const slots = CATEGORICAL[theme];
  return slots[index % slots.length]!;
}

export function optionColor(theme: Theme, name: string | null | undefined, fallbackIndex: number): string {
  return (name && OPTION_HEX[theme][name]) || seriesColor(theme, fallbackIndex);
}
