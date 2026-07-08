import type { ColdStartStatus } from "../hooks/useColdStartGuard";

export function ColdStartBanner({ status }: { status: ColdStartStatus }): React.JSX.Element | null {
  if (status === "warm" || status === "checking") {
    return null;
  }

  const message =
    status === "cold"
      ? "Waking up the agent service — this can take up to a minute on the free tier. Go ahead and ask your question; it will go through once the service is ready."
      : "The agent service isn't responding yet. It may still be starting up on the free tier — try asking your question, or reload in a minute.";

  return (
    <div className="cold-start-banner" role="status">
      {message}
    </div>
  );
}
