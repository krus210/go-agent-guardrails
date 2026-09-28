package deadlock

import (
	"sync"
	"testing"
)

// TestMutexDeadlock intentionally hangs until go test's timeout expires.
// The timeout is not a synctest deadlock diagnostic.
func TestMutexDeadlock(t *testing.T) {
	var mu sync.Mutex
	mu.Lock()
	defer mu.Unlock()

	mu.Lock() // Waits for the same mutex, which this goroutine has not released.
}
