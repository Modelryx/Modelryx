import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

export type RevisionSectionLink = { id: string; label: string; group: string };

export function RevisionIndexTabs({ sections, activeSection, basePath, versionId, actions }: {
  sections: RevisionSectionLink[];
  activeSection: string;
  basePath: string;
  versionId?: string;
  actions?: (closeMenu: () => void) => ReactNode;
}) {
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [alignMenuRight, setAlignMenuRight] = useState(false);
  const navRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonsRef = useRef(new Map<string, HTMLButtonElement>());
  const groups = [...new Set([...sections.map(section => section.group), ...(actions ? ["Actions"] : [])])];
  const activeGroup = sections.find(section => section.id === activeSection)?.group;

  useEffect(() => { setOpenGroup(null); }, [activeSection, basePath]);
  useEffect(() => {
    if (!openGroup) return;
    const outside = (event: PointerEvent) => {
      if (!navRef.current?.contains(event.target as Node)) setOpenGroup(null);
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpenGroup(null);
      buttonsRef.current.get(openGroup)?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [openGroup]);

  const focusMenu = (last = false) => {
    window.requestAnimationFrame(() => {
      const entries = menuRef.current?.querySelectorAll<HTMLElement>('a, button[role="menuitem"]:not(:disabled)');
      (last ? entries?.[entries.length - 1] : entries?.[0])?.focus();
    });
  };
  const positionMenu = (button: HTMLButtonElement) => {
    const { left, right } = button.getBoundingClientRect();
    setAlignMenuRight(window.innerWidth - left < 216 && right >= 216);
  };
  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const entries = [...(menuRef.current?.querySelectorAll<HTMLElement>('a, button[role="menuitem"]:not(:disabled)') ?? [])];
    if (!entries.length) return;
    const index = entries.indexOf(document.activeElement as HTMLElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      entries[(index + (event.key === "ArrowDown" ? 1 : -1) + entries.length) % entries.length]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      entries[event.key === "Home" ? 0 : entries.length - 1]?.focus();
    }
  };

  return <nav ref={navRef} className="revision-index-navigation" aria-label="Revision sections"
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpenGroup(null); }}>
    <div className="revision-index-tabs">
      {groups.map((group, index) => {
        const choices = sections.filter(section => section.group === group);
        const open = openGroup === group;
        return <div className="revision-index-tab" key={group}>
          <button type="button"
            ref={element => { if (element) buttonsRef.current.set(group, element); else buttonsRef.current.delete(group); }}
            className={activeGroup === group ? "is-active" : ""}
            aria-current={activeGroup === group ? "location" : undefined}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-controls={open ? `revision-index-menu-${index}` : undefined}
            onClick={event => {
              if (openGroup !== group) positionMenu(event.currentTarget);
              setOpenGroup(openGroup === group ? null : group);
            }}
            onKeyDown={event => {
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                positionMenu(event.currentTarget);
                setOpenGroup(group);
                focusMenu(event.key === "ArrowUp");
              }
            }}
          >{group}<span aria-hidden="true">▾</span></button>
          {(open || group === "Actions") && <div ref={open ? menuRef : undefined} hidden={!open} id={`revision-index-menu-${index}`} className={`revision-index-menu${alignMenuRight ? " align-right" : ""}`} role="menu" aria-label={`${group} ${group === "Actions" ? "commands" : "sections"}`} onKeyDown={onMenuKeyDown}>
            {group === "Actions" ? actions?.(() => { setOpenGroup(null); buttonsRef.current.get("Actions")?.focus(); }) : choices.map(section => <Link key={section.id} role="menuitem" aria-current={section.id === activeSection ? "page" : undefined}
              to={`${basePath}?${new URLSearchParams({
                ...(section.id === "overview" ? {} : { section: section.id }),
                ...(versionId && ["overview", "versions", "preview", "structure", "revision-files"].includes(section.id)
                  ? { version: versionId } : {}),
              })}`}
              onClick={() => setOpenGroup(null)}>{section.label}{section.id === activeSection && <span aria-hidden="true">✓</span>}</Link>)}
          </div>}
        </div>;
      })}
    </div>
  </nav>;
}
