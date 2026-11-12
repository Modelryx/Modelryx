import { useEffect, useState } from 'react';
import { api } from '../api';
import { Button } from './Button';

export type Resource<T> = { data?: T; loading: boolean; error: string; retry: () => void };
export function useResource<T>(path: string | null): Resource<T> {
  const [result, setResult] = useState<{ path: string; value: T }>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setError('');
    if (!path) {
      setLoading(false);
      return () => {
        active = false;
      };
    }
    setLoading(true);
    api<T>(path)
      .then((value) => {
        if (active) setResult({ path, value });
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Unable to load data');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, attempt]);
  return {
    data: result?.path === path ? result.value : undefined,
    loading: !!path && (loading || (!!result && result.path !== path && !error)),
    error,
    retry: () => setAttempt((value) => value + 1),
  };
}
export function ResourceState<T>({ state }: { state: Resource<T> }) {
  if (state.loading)
    return (
      <p className="section-state" role="status">
        Loading…
      </p>
    );
  if (state.error)
    return (
      <div className="section-error" role="alert">
        <span>{state.error}</span>
        <Button variant="secondary" onClick={state.retry}>
          Retry
        </Button>
      </div>
    );
  return null;
}
