"use client";

import { useSyncExternalStore } from "react";

import type { Locale } from "@/lib/types";

const LOCALE_STORAGE_KEY = "enforge.locale";
const LOCALE_EVENT = "enforge:locale-change";

let inMemoryLocale: Locale | null = null;

export function getLocaleSnapshot(): Locale {
  if (inMemoryLocale) return inMemoryLocale;
  try {
    const saved = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    if (saved === "zh" || saved === "en") {
      inMemoryLocale = saved;
      return saved;
    }
  } catch {
    // localStorage can be unavailable in private browsing contexts.
  }
  const preferred = navigator.language.toLowerCase().startsWith("zh")
    ? "zh"
    : "en";
  inMemoryLocale = preferred;
  return preferred;
}

function getServerLocaleSnapshot(): Locale {
  return "zh";
}

function subscribeToLocale(onStoreChange: () => void): () => void {
  window.addEventListener("storage", onStoreChange);
  window.addEventListener(LOCALE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("storage", onStoreChange);
    window.removeEventListener(LOCALE_EVENT, onStoreChange);
  };
}

export function persistLocale(locale: Locale): void {
  inMemoryLocale = locale;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Keep the in-memory preference for the current session.
  }
  window.dispatchEvent(new Event(LOCALE_EVENT));
}

export function useLocale(): Locale {
  return useSyncExternalStore(
    subscribeToLocale,
    getLocaleSnapshot,
    getServerLocaleSnapshot,
  );
}

export function pick(locale: Locale, zh: string, en: string): string {
  return locale === "zh" ? zh : en;
}
