"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch, apiUrl } from "@/lib/api";
import type { EventListResponse, RunEvent } from "@/lib/types";

const EVENT_NAMES = [
  "run_created",
  "cancel_requested",
  "retry_scheduled",
  "phase_started",
  "phase_completed",
  "phase_failed",
  "progress",
  "source_fetched",
  "source_parsed",
  "evidence_extracted",
  "section_generated",
  "citation_audit_failed",
  "awaiting_approval",
  "approval_decided",
  "run_cancelled",
  "error",
  "other",
];

export type StreamStatus =
  | "loading"
  | "connecting"
  | "live"
  | "reconnecting"
  | "closed";

export function useRunEventStream({
  runId,
  active,
  onEvent,
}: {
  runId: string;
  active: boolean;
  onEvent?: (event: RunEvent) => void;
}) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const lastIdRef = useRef(0);
  const eventsRef = useRef<RunEvent[]>([]);
  const sourceRef = useRef<EventSource | null>(null);
  const retryTimer = useRef<number | null>(null);
  const onEventRef = useRef(onEvent);
  const connectRef = useRef<() => void>(() => {});

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  const applyEvent = useCallback((event: RunEvent) => {
    if (!Number.isFinite(event.id) || event.id <= lastIdRef.current) return;
    if (eventsRef.current.some((item) => item.id === event.id)) return;
    lastIdRef.current = event.id;
    const next = [...eventsRef.current, event].sort((a, b) => a.id - b.id);
    eventsRef.current = next;
    setEvents(next);
    onEventRef.current?.(event);
  }, []);

  const connect = useCallback(() => {
    if (!active || typeof window === "undefined") return;
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    sourceRef.current?.close();
    setError(null);
    setStatus("connecting");

    const source = new EventSource(
      apiUrl(
        `/runs/${runId}/events/stream?after=${Math.max(0, lastIdRef.current)}`,
      ),
    );
    sourceRef.current = source;
    source.onopen = () => {
      setStatus("live");
      setError(null);
    };
    source.onerror = () => {
      setStatus("reconnecting");
      if (
        source.readyState === EventSource.CLOSED &&
        retryTimer.current === null
      ) {
        retryTimer.current = window.setTimeout(() => {
          retryTimer.current = null;
          connectRef.current();
        }, 3000);
      }
    };
    for (const name of EVENT_NAMES) {
      source.addEventListener(name, (message) => {
        try {
          const payload = JSON.parse(
            (message as MessageEvent<string>).data,
          ) as RunEvent;
          applyEvent(payload);
        } catch {
          // Ignore malformed frames; the stream retries on its own.
        }
      });
    }
  }, [active, applyEvent, runId]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        const response = await apiFetch<EventListResponse>(
          `/runs/${runId}/events?limit=1000`,
        );
        if (cancelled) return;
        const items = response.items;
        eventsRef.current = items;
        setEvents(items);
        lastIdRef.current = items.length
          ? Math.max(...items.map((item) => item.id))
          : 0;
      } catch {
        if (!cancelled) {
          setError("Event history could not be loaded");
        }
      }
      if (cancelled) return;
      if (active) {
        connect();
      } else {
        setStatus("closed");
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
      sourceRef.current?.close();
      sourceRef.current = null;
      if (retryTimer.current !== null) {
        window.clearTimeout(retryTimer.current);
        retryTimer.current = null;
      }
    };
  }, [active, connect, runId]);

  return { events, status, error, reconnect: connect };
}
