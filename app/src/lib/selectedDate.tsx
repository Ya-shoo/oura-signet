// The day the Home view is showing (null = today/latest). Shared via context so
// nested cards (e.g. the activity strip inside the Movement tile) scope to the
// same day as the rest of Home without prop-drilling.
import { createContext, useContext } from "react";

export const SelectedDateContext = createContext<string | null>(null);
export const useSelectedDate = () => useContext(SelectedDateContext);
