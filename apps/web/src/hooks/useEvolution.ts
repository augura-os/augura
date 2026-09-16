import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { DerivationVerdict } from "@shared";
import { fetchEvolution, updateDerivation } from "../services/api";

export function useEvolution(creativeId: string) {
  return useQuery({
    queryKey: ["evolution", creativeId],
    queryFn: () => fetchEvolution(creativeId),
    enabled: Boolean(creativeId),
  });
}

export function useUpdateDerivation(creativeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ derivationId, verdict }: { derivationId: string; verdict: DerivationVerdict }) =>
      updateDerivation(derivationId, { verdict }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["evolution", creativeId] });
    },
  });
}
