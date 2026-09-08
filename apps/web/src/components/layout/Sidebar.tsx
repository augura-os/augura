import { NavLink } from "react-router-dom";
import { FolderOpen, Github, Network, Settings, Upload } from "lucide-react";
import { cn } from "../../lib/utils";

const navItems = [
  { to: "/", label: "Graph", icon: Network, end: true },
  { to: "/upload", label: "Upload", icon: Upload, end: false },
  { to: "/assets", label: "Assets", icon: FolderOpen, end: false },
  { to: "/settings", label: "Settings", icon: Settings, end: false },
];

/** Slim left sidebar navigation (§9 UI rules). */
export function Sidebar() {
  return (
    <aside className="flex w-20 shrink-0 flex-col items-center gap-1 border-r border-[#e5e5e5] bg-white py-4">
      <div className="mb-4 flex h-8 w-8 items-center justify-center rounded-md bg-neutral-900 text-white">
        {/* Augura 眼睛标（小尺寸变体：无信号点、加粗线条） */}
        <svg viewBox="0 0 200 200" className="h-5 w-5" aria-label="Augura">
          <g fill="none" stroke="currentColor" strokeWidth="14" strokeLinecap="round">
            <path d="M 20 100 Q 96 40 160 94" />
            <path d="M 20 100 Q 96 160 160 106" />
            <circle cx="90" cy="100" r="32" />
          </g>
          <circle cx="90" cy="100" r="12" fill="currentColor" />
        </svg>
      </div>
      {navItems.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            cn(
              "flex w-16 flex-col items-center gap-1 rounded-md py-2 text-[11px] font-medium",
              isActive
                ? "bg-neutral-100 text-neutral-900"
                : "text-neutral-500 hover:bg-neutral-50 hover:text-neutral-700",
            )
          }
        >
          <Icon className="h-4 w-4" />
          <span>{label}</span>
        </NavLink>
      ))}
      {/* 版权信息（LICENSE 条款 1b 要求保留，勿移除） */}
      <a
        href="https://github.com/augura-os/augura"
        target="_blank"
        rel="noreferrer"
        title="Augura on GitHub"
        className="mt-auto flex flex-col items-center gap-0.5 pt-2 text-[9px] leading-tight text-neutral-400 transition hover:text-neutral-600"
      >
        <Github className="h-3.5 w-3.5" />
        <span>© 2026</span>
        <span>Augura</span>
      </a>
    </aside>
  );
}
