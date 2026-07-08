import { useCallback, useEffect, useState } from "react";
import { probeHealth } from "../api/client";

export type ColdStartStatus = "checking" | "warm" | "cold" | "unreachable";

const SLOW_THRESHOLD_MS = 2500; // "more than 2-3 seconds" from the design brief
const PROBE_TIMEOUT_MS = 65_000; // Render's documented worst case is ~60s

/**
 * Probes /health on mount and classifies the backend as warm, waking up
 * ("cold"), or unreachable — WITHOUT waiting for a hard timeout to tell the
 * user anything. If the probe hasn't resolved within SLOW_THRESHOLD_MS we
 * assume a cold start is in progress and surface that immediately; the
 * banner then updates again once the real result (or a 503) comes back.
 */
export function useColdStartGuard(): {
  status: ColdStartStatus;
  markWarm: () => void;
} {
  const [status, setStatus] = useState<ColdStartStatus>("checking");

  useEffect(() => {
    let cancelled = false;

    const slowTimer = setTimeout(() => {
      setStatus((current) => (current === "checking" ? "cold" : current));
    }, SLOW_THRESHOLD_MS);

    probeHealth(PROBE_TIMEOUT_MS)
      .then((result) => {
        if (cancelled) return;
        setStatus(result.reachable && result.healthy ? "warm" : "unreachable");
      })
      .finally(() => clearTimeout(slowTimer));

    return () => {
      cancelled = true;
      clearTimeout(slowTimer);
    };
  }, []);

  // Called after any successful /ask response: that round trip is itself
  // proof the full stack (API + DB) is up, regardless of what the probe
  // above is still doing.
  const markWarm = useCallback(() => setStatus("warm"), []);

  return { status, markWarm };
}
