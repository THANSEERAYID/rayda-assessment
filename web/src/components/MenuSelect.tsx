import { useEffect, useId, useRef, useState } from "react";

export type MenuSelectOption = {
  value: string;
  label: string;
  meta?: string;
  /** Single character shown in the avatar mark. Defaults to first letter of label. */
  mark?: string;
};

/**
 * Custom listbox used by the sidebar tenant picker and module filters —
 * same interaction and chrome, dark or light tone.
 */
export function MenuSelect({
  value,
  options,
  onChange,
  labelledBy,
  label,
  tone = "dark",
  disabled = false,
  emptyLabel = "No options",
  className = "",
  showMark = true,
}: {
  value: string;
  options: MenuSelectOption[];
  onChange: (value: string) => void;
  labelledBy?: string;
  label?: string;
  tone?: "dark" | "light";
  disabled?: boolean;
  emptyLabel?: string;
  className?: string;
  showMark?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const labelId = useId();
  const selected = options.find((o) => o.value === value) ?? options[0];
  const ariaLabelledBy = labelledBy ?? (label ? labelId : undefined);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  const markFor = (option: MenuSelectOption | undefined) => {
    if (!option) return "?";
    if (option.mark?.trim()) return option.mark.trim().charAt(0).toUpperCase();
    return (option.label ?? "?").trim().charAt(0).toUpperCase() || "?";
  };

  return (
    <div
      className={`menu-select-field menu-select-field--${tone} ${className}`.trim()}
    >
      {label && (
        <span className="menu-select-label" id={labelId}>
          {label}
        </span>
      )}
      <div
        className={`menu-select menu-select--${tone}${open ? " is-open" : ""}${
          disabled ? " is-disabled" : ""
        }`}
        ref={rootRef}
      >
        <button
          type="button"
          className="menu-select-trigger"
          disabled={disabled || options.length === 0}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listId}
          aria-labelledby={ariaLabelledBy}
          onClick={() => setOpen((v) => !v)}
        >
          {showMark && (
            <span className="menu-select-mark" aria-hidden>
              {markFor(selected)}
            </span>
          )}
          <span className="menu-select-copy">
            <span className="menu-select-name">
              {selected?.label ?? emptyLabel}
            </span>
            {selected?.meta && (
              <span className="menu-select-meta">{selected.meta}</span>
            )}
          </span>
          <span className="menu-select-chevron" aria-hidden />
        </button>

        {open && (
          <ul
            id={listId}
            className="menu-select-menu"
            role="listbox"
            aria-labelledby={ariaLabelledBy}
          >
            {options.map((option) => {
              const on = option.value === value;
              return (
                <li key={option.value} role="presentation">
                  <button
                    type="button"
                    role="option"
                    aria-selected={on}
                    className={`menu-select-option${on ? " is-on" : ""}`}
                    onClick={() => {
                      onChange(option.value);
                      setOpen(false);
                    }}
                  >
                    {showMark && (
                      <span className="menu-select-option-mark" aria-hidden>
                        {markFor(option)}
                      </span>
                    )}
                    <span className="menu-select-option-copy">
                      <span className="menu-select-option-name">
                        {option.label}
                      </span>
                      {option.meta && (
                        <span className="menu-select-option-meta">
                          {option.meta}
                        </span>
                      )}
                    </span>
                    {on && <span className="menu-select-check" aria-hidden />}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
