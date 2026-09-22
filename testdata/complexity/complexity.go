package complexity

func CanExport(active, allowed, ready bool) bool {
	if active {
		if allowed {
			if ready {
				return true
			}
		}
	}
	return false
}
