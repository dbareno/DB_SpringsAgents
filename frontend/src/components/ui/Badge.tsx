import clsx from 'clsx';

type BadgeVariant = 'success' | 'error' | 'warning' | 'info' | 'neutral';

interface BadgeProps {
  variant?: BadgeVariant;
  children: React.ReactNode;
  className?: string;
}

const variantStyles: Record<BadgeVariant, string> = {
  success: 'bg-emerald-900/60 text-emerald-300 border-emerald-700/50',
  error: 'bg-red-900/60 text-red-300 border-red-700/50',
  warning: 'bg-amber-900/60 text-amber-300 border-amber-700/50',
  info: 'bg-blue-900/60 text-blue-300 border-blue-700/50',
  neutral: 'bg-zinc-800 text-zinc-300 border-zinc-700',
};

export default function Badge({ variant = 'neutral', children, className }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium',
        variantStyles[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
