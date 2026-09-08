import { useQuery } from "@tanstack/react-query";
import { fetchDnas } from "../services/api";

export function useDnas() {
  return useQuery({
    queryKey: ["dnas"],
    queryFn: fetchDnas,
  });
}
