package policy

func Allowed(size int) bool {
	return size <= 10
}
