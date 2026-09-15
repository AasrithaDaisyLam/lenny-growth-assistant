import { useCallback, useEffect, useState } from "react";

import * as api from "../api/client";
import type { HealthResponse, ProvidersResponse } from "../types";
import { errorText } from "./useSessions";

/** Provider catalogue and dependency health, both polled on mount. */
export function useProviders() {
	const [providers, setProviders] = useState<ProvidersResponse | null>(null);
	const [health, setHealth] = useState<HealthResponse | null>(null);
	const [error, setError] = useState<string | null>(null);

	const refresh = useCallback(async () => {
		try {
			const [catalogue, readiness] = await Promise.all([api.getProviders(), api.getHealth()]);
			setProviders(catalogue);
			setHealth(readiness);
			setError(null);
		} catch (cause) {
			setError(errorText(cause));
		}
	}, []);

	useEffect(() => {
		void refresh();
	}, [refresh]);

	return { providers, health, error, refresh };
}
