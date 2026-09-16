import { useQuery } from "@tanstack/react-query";
import { fetchAssets } from "../services/api";

/**
 * Asset list with search. Polls every ~4s while any row is still
 * pending/processing so AI analysis status updates live.
 */
export function useAssets(search: string) {
  return useQuery({
    queryKey: ["assets", "list", search],
    queryFn: () => fetchAssets(search || undefined),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const busy = data.some(
        (asset) => asset.analysis_status === "pending" || asset.analysis_status === "processing",
      );
      return busy ? 4000 : false;
    },
  });
}
