package policy

import "testing"

func TestAllowed(t *testing.T) {
	for _, tc := range []struct {
		name string
		size int
		want bool
	}{
		{name: "below", size: 9, want: true},
		{name: "boundary", size: 10, want: true},
		{name: "above", size: 11, want: false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := Allowed(tc.size); got != tc.want {
				t.Fatalf("Allowed(%d) = %v, want %v", tc.size, got, tc.want)
			}
		})
	}
}
