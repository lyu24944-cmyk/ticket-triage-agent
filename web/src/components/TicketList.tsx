type Item = {
  ticket_id: string;
  subject: string;
  meta: string;
};

type Props = {
  title: string;
  items: Item[];
  selectedId: string | null;
  nextCursor: string | null;
  loadingMore?: boolean;
  onSelect: (ticketId: string) => void;
  onLoadMore?: () => void;
};

export function TicketList({
  title,
  items,
  selectedId,
  nextCursor,
  loadingMore,
  onSelect,
  onLoadMore,
}: Props) {
  return (
    <div className="block">
      <h2>
        {title}
        {items.length ? ` · ${items.length}` : ""}
      </h2>
      {items.length === 0 ? (
        <p className="empty">—</p>
      ) : (
        <ul className="list">
          {items.map((item) => (
            <li
              key={item.ticket_id}
              className={item.ticket_id === selectedId ? "active" : undefined}
              onClick={() => onSelect(item.ticket_id)}
            >
              <strong>{item.subject}</strong>
              <div className="meta">{item.meta}</div>
            </li>
          ))}
        </ul>
      )}
      {nextCursor ? (
        <div className="actions">
          <button
            type="button"
            className="ghost"
            disabled={loadingMore}
            onClick={onLoadMore}
          >
            {loadingMore ? "…" : "更多"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
