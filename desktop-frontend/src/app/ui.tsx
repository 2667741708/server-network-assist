import type { ReactNode } from 'react';
import { Button, Caption1, Subtitle2, Title2 } from '@fluentui/react-components';
import type { HealthLevel } from '../api/types';

export function formatBytes(value: number | null | undefined): string {
  if (!Number.isFinite(value)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let number = value as number;
  let index = 0;
  while (number >= 1024 && index < units.length - 1) {
    number /= 1024;
    index += 1;
  }
  return `${number.toFixed(index ? 1 : 0)} ${units[index]}`;
}

export function formatRate(value: number | null | undefined): string {
  return `${formatBytes(value)}/s`;
}

export function formatTime(value: number | null | undefined): string {
  if (!value) return '—';
  return new Date(value * 1000).toLocaleString();
}

export function formatClock(value: number | null | undefined): string {
  if (!value) return '—';
  return new Date(value * 1000).toLocaleTimeString();
}

export function formatAge(timestamp: number | null | undefined, now: number | null | undefined): string {
  if (!timestamp || !now) return '暂无握手信息';
  const seconds = Math.max(0, Math.floor(now - timestamp));
  if (seconds < 60) return `约 ${seconds} 秒前`;
  if (seconds < 3600) return `约 ${Math.floor(seconds / 60)} 分钟前`;
  return `约 ${Math.floor(seconds / 3600)} 小时前`;
}

export function unknown(value: string | number | null | undefined): string {
  return value === undefined || value === null || value === '' ? '未知' : String(value);
}

export function healthLabel(level: HealthLevel): string {
  return level === 'ok' ? '正常' : level === 'warning' ? 'Warning' : level === 'danger' ? 'Failed' : 'Unknown';
}

export function StatusPill({level, children}: {level: HealthLevel; children: ReactNode}) {
  return <span className={`status-pill ${level}`}><span className={`status-dot ${level}`} aria-hidden="true" />{children}</span>;
}

export function PageIntro({title, description, action}: {title: string; description: string; action?: ReactNode}) {
  return <div className="page-intro">
    <div>
      <Title2>{title}</Title2>
      <p>{description}</p>
    </div>
    {action}
  </div>;
}

export function Surface({title, description, action, children, className = ''}: {
  title?: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return <section className={`surface ${className}`.trim()}>
    {(title || description || action) && <div className="surface-header">
      <div>
        {title && <Subtitle2>{title}</Subtitle2>}
        {description && <Caption1>{description}</Caption1>}
      </div>
      {action}
    </div>}
    <div className="surface-body">{children}</div>
  </section>;
}

export function ButtonRow({children}: {children: ReactNode}) {
  return <div className="table-actions">{children}</div>;
}

export function EmptyState({children}: {children: ReactNode}) {
  return <div className="empty-state">{children}</div>;
}

export function DangerButton({children, onClick, disabled, title}: {children: ReactNode; onClick?: () => void; disabled?: boolean; title?: string}) {
  return <Button appearance="outline" onClick={onClick} disabled={disabled} title={title} className="danger-button">{children}</Button>;
}

export function ExternalLink({href, children}: {href: string; children: ReactNode}) {
  return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
}
