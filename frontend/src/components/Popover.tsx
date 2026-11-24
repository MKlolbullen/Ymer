import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type Props = {
  /** The element the popover opens next to. */
  anchor: HTMLElement | null;
  onClose: () => void;
  children: ReactNode;
  width?: number;
  label?: string;
};

/**
 * A floating panel next to `anchor`, rendered in a portal with fixed positioning so
 * scroll containers (the table) do not clip it. Closes on Escape or a click outside.
 */
export function Popover({ anchor, onClose, children, width = 260, label }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useLayoutEffect(() => {
    if (!anchor) return;
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const height = ref.current?.offsetHeight ?? 0;
      const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
      const below = rect.bottom + 4;
      const top = below + height > window.innerHeight - 8 && rect.top > height ? rect.top - height - 4 : below;
      setPos({ top, left });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [anchor, width]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target) || anchor?.contains(target)) return;
      // Clicks inside another popover (a nested picker) do not close this one.
      if ((target as HTMLElement).closest?.("[data-popover]")) return;
      close.current();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close.current();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [anchor]);

  if (!anchor) return null;
  return createPortal(
    <div
      ref={ref}
      data-popover=""
      role="dialog"
      aria-label={label}
      style={{ top: pos?.top ?? -9999, left: pos?.left ?? -9999, width }}
      className="fixed z-50 max-h-[70vh] overflow-y-auto rounded-lg border border-zinc-200 bg-white p-1 text-sm shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
    >
      {children}
    </div>,
    document.body,
  );
}

/** A full-width row button for menus inside a popover. */
export function MenuItem({
  onClick,
  children,
  danger,
  active,
  disabled,
}: {
  onClick: () => void;
  children: ReactNode;
  danger?: boolean;
  active?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-zinc-100 disabled:opacity-50 dark:hover:bg-zinc-800 ${
        danger ? "text-rose-600" : ""
      } ${active ? "bg-zinc-100 dark:bg-zinc-800" : ""}`}
    >
      {children}
    </button>
  );
}

/** Form field look without a width, for fields laid out side by side. */
export const fieldClass =
  "rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm focus:border-brand-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950";
export const inputClass = `w-full ${fieldClass}`;
