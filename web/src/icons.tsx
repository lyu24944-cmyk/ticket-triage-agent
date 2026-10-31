type Props = { className?: string };

export function IconSearch({ className }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="16" height="16" aria-hidden>
      <circle cx="11" cy="11" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.75" />
      <path d="M16.2 16.2 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
    </svg>
  );
}

export function IconRefresh({ className }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="16" height="16" aria-hidden>
      <path
        d="M19.5 12a7.5 7.5 0 0 1-12.8 5.3M4.5 12a7.5 7.5 0 0 1 12.8-5.3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
      <path d="M16.2 4.2h3.2V7.4M7.8 19.8H4.6v-3.2" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
