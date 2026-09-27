// Suggest a browser time zone only during a new installation's initial setup.
export function timeZoneSettings(payload, detect = () => Intl.DateTimeFormat().resolvedOptions().timeZone) {
  const values = { ...payload.values };
  if (payload.browser_time_zone_pending && payload.auth?.password_is_default) {
    try {
      const timeZone = detect();
      if (timeZone) {
        new Intl.DateTimeFormat(undefined, { timeZone });
        values.time_zone = timeZone;
      }
    } catch {
      // Keep the server default when browser detection is unavailable.
    }
  }
  const timeZoneOptions = [...(payload.time_zone_options || [])];
  if (values.time_zone && !timeZoneOptions.some(option => option.value === values.time_zone)) {
    timeZoneOptions.push({ value: values.time_zone, label: values.time_zone.replaceAll("_", " ") });
  }
  return { values, timeZoneOptions };
}
