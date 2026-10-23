import { useCallback, useEffect, useRef, useState } from 'react';

interface Resource<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => void;
}

/** Fetch on mount and whenever `deps` change; stale responses are ignored. */
export function useResource<T>(load: () => Promise<T>, deps: unknown[]): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);

  useEffect(() => {
    const mine = ++seq.current;
    setLoading(true);
    setError(undefined);
    load()
      .then((value) => { if (mine === seq.current) setData(value); })
      .catch((err: Error) => { if (mine === seq.current) setError(err); })
      .finally(() => { if (mine === seq.current) setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
