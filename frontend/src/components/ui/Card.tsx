import clsx from 'clsx';

interface CardProps {
  children: React.ReactNode;
  className?: string;
  title?: string;
}

export default function Card({ children, className, title }: CardProps) {
  return (
    <div
      className={clsx(
        'rounded-xl border border-zinc-800 bg-[#161b22] p-5',
        className
      )}
    >
      {title && (
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wider text-zinc-400">
          {title}
        </h3>
      )}
      {children}
    </div>
  );
}
