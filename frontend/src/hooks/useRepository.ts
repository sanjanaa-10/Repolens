import { useEffect, useState } from "react";
import { getRepository } from "../api/client";
import type { RepositoryInfo } from "../types";

interface UseRepositoryResult {
  repo: RepositoryInfo | null;
  loading: boolean;
  error: string | null;
}

export function useRepository(id: string | undefined): UseRepositoryResult {
  const [repo, setRepo] = useState<RepositoryInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setRepo(null);
    setError(null);
    setLoading(true);
    if (!id) {
      if (active) setLoading(false);
      return;
    }
    getRepository(Number(id))
      .then((data) => {
        if (active) {
          setRepo(data);
          setLoading(false);
        }
      })
      .catch(() => {
        if (active) {
          setError("Repository not found.");
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [id]);

  return { repo, loading, error };
}