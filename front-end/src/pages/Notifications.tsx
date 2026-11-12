import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type UserNotification } from '../workflow-api';

function dateLabel(value: unknown): string {
  if (typeof value !== 'string') return '';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(
    new Date(value)
  );
}

export function Notifications() {
  const [rows, setRows] = useState<UserNotification[]>();
  const [error, setError] = useState('');

  const load = () =>
    api<UserNotification[]>('/me/notifications')
      .then(setRows)
      .catch((reason) => setError(reason.message));

  useEffect(() => {
    void load();
  }, []);

  const markRead = async (row: UserNotification) => {
    try {
      if (!row.read_at) await api(`/me/notifications/${row.id}/read`, 'POST', {});
      window.dispatchEvent(new Event('modelryx-notifications-changed'));
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to update notification');
    }
  };

  const markAllRead = async () => {
    try {
      await api('/me/notifications/read-all', 'POST', {});
      window.dispatchEvent(new Event('modelryx-notifications-changed'));
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to update notifications');
    }
  };

  return (
    <section className="page notifications-page">
      <div className="notifications-heading">
        <div>
          <p className="eyebrow">PERSONAL</p>
          <h1>Notifications</h1>
        </div>
        <button onClick={() => void markAllRead()} disabled={!rows?.some((row) => !row.read_at)}>
          Mark all read
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!rows && !error && <p>Loading…</p>}
      {rows?.length === 0 && (
        <div className="notifications-empty">
          <h2>You’re all caught up</h2>
          <p>Workflow updates and checkout warnings will appear here.</p>
        </div>
      )}
      <div className="notification-list">
        {rows?.map((row) => {
          const itemId = typeof row.details.item_id === 'string' ? row.details.item_id : null;
          const revisionId =
            typeof row.details.revision_id === 'string' ? row.details.revision_id : null;
          const expiresAt = row.details.expires_at;
          const dueAt = row.details.due_at;
          return (
            <article
              key={row.id}
              className={`notification-card ${row.read_at ? 'is-read' : 'is-unread'}`}
            >
              <div className="notification-card-header">
                <h2>{row.title}</h2>
                {!row.read_at && <span className="notification-unread">New</span>}
              </div>
              <p>{row.message}</p>
              {expiresAt != null && (
                <p className="notification-meta">Expires {dateLabel(expiresAt)}</p>
              )}
              {dueAt != null && <p className="notification-meta">Due {dateLabel(dueAt)}</p>}
              <div className="notification-actions">
                {itemId && revisionId ? (
                  <Link
                    className="button"
                    to={`/items/${itemId}/revisions/${revisionId}`}
                    onClick={() => void markRead(row)}
                  >
                    Open revision
                  </Link>
                ) : (
                  itemId && (
                    <Link className="button" to={`/items/${itemId}`}>
                      Open item
                    </Link>
                  )
                )}
                {!row.read_at && <button onClick={() => void markRead(row)}>Mark read</button>}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
