/**
 * Whether this build runs inside the Ymer phone apps (desktop/, built with
 * VITE_YMER_APP=mobile). There the app is served by the app's own relay, which also
 * keeps offline copies of API reads, so the service worker is off, and the relay's
 * server picker lives at /__ymer/.
 */
export const IN_APP = import.meta.env.VITE_YMER_APP === "mobile";

/** The relay's page for choosing another Ymer server. */
export const CHANGE_SERVER_URL = "/__ymer/?change=1";
