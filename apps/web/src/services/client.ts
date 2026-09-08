import axios, { AxiosError } from "axios";

// Contract §3: relative paths only — dev uses the vite proxy, docker uses nginx.
export const apiClient = axios.create({
  baseURL: "/",
  timeout: 60000,
});

// Non-2xx responses carry the envelope's human-readable message
// (e.g. merge-guard blocks); surface it instead of the generic
// "Request failed with status code N" so callers can branch on it.
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ message?: string }>) => {
    const envelopeMessage = error.response?.data?.message;
    if (envelopeMessage) {
      return Promise.reject(new Error(envelopeMessage));
    }
    return Promise.reject(error);
  },
);
