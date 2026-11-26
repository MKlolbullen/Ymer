/**
 * The app's icons: one Lucide set (https://lucide.dev), drawn as 1.75px strokes in the
 * current text colour, so they follow every theme. Pages, rows, property types and
 * templates map to icons here so the sidebar, search, menus and mentions all agree.
 */
import {
  AlignLeft,
  ArrowUpRight,
  BookOpen,
  Bug,
  Calendar,
  CalendarDays,
  CalendarRange,
  CircleDot,
  Factory,
  FileText,
  Folder,
  GitBranch,
  Hash,
  Lightbulb,
  Link,
  ListChecks,
  Network,
  Paperclip,
  PenTool,
  Repeat,
  Rocket,
  Rows3,
  Scale,
  Sigma,
  Square,
  SquareCheck,
  SquareFunction,
  Table2,
  Tags,
  Target,
  Type,
  User,
  Users,
  type LucideIcon,
  type LucideProps,
} from "lucide-react";

import type { PropertyType } from "@/lib/types";

export type { LucideIcon };

const DEFAULTS = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;

/** A Lucide icon with the app's defaults: 16px, 1.75px stroke, hidden from screen readers. */
export function Icon({ icon: Glyph, className, ...props }: { icon: LucideIcon } & LucideProps) {
  return <Glyph {...DEFAULTS} className={`shrink-0 ${className ?? ""}`} {...props} />;
}

export const PAGE_ICONS: Record<string, LucideIcon> = {
  document: FileText,
  folder: Folder,
  database: Table2,
  mindmap: Network,
  drawing: PenTool,
  factory: Factory,
  row: Rows3,
};

/** The icon for a page (or search result) type; unknown types get the document icon. */
export function PageIcon({ type, ...props }: { type: string } & LucideProps) {
  return <Icon icon={PAGE_ICONS[type] ?? FileText} {...props} />;
}

export const PROPERTY_ICONS: Record<PropertyType, LucideIcon> = {
  title: Type,
  text: AlignLeft,
  number: Hash,
  select: CircleDot,
  multi_select: Tags,
  date: Calendar,
  checkbox: SquareCheck,
  url: Link,
  person: User,
  relation: ArrowUpRight,
  rollup: Sigma,
  formula: SquareFunction,
  date_range: CalendarRange,
  file: Paperclip,
};

export function PropertyIcon({ type, ...props }: { type: PropertyType } & LucideProps) {
  return <Icon icon={PROPERTY_ICONS[type] ?? AlignLeft} {...props} />;
}

/** Built-in templates by id. The server also sends an emoji, used for templates not listed here. */
const TEMPLATE_ICONS: Record<string, LucideIcon> = {
  "meeting-notes": Users,
  "project-brief": Target,
  "weekly-plan": CalendarDays,
  "decision-record": Scale,
  retro: Repeat,
  "task-tracker": ListChecks,
  "reading-list": BookOpen,
  "bug-tracker": Bug,
  brainstorm: Lightbulb,
  "getting-started": Rocket,
  personal: User,
  "product-team": GitBranch,
  "team-wiki": BookOpen,
  blank: Square,
};

export function TemplateIcon({ id, fallback, ...props }: { id: string; fallback?: string } & LucideProps) {
  const glyph = TEMPLATE_ICONS[id];
  if (glyph) return <Icon icon={glyph} {...props} />;
  return <span aria-hidden>{fallback ?? ""}</span>;
}

